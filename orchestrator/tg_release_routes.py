"""Add Skryon Windows release staging/publishing to the EXISTING aiogram bot.

Call register_release_routes(dp) from orchestrator/tg_admin.py before the
catch-all handler. No second bot token or competing getUpdates poller.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from pathlib import Path

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import is_admin
from release_core import (CHUNK_RE, MANIFEST_RE, ReleaseError,
                          ReleaseStore, validate_manifest)

log = logging.getLogger(__name__)
_STORE = ReleaseStore(
    Path(os.getenv('SKRYON_RELEASE_STAGING_DIR', '/var/lib/skryon-releases')),
    Path(os.getenv('SKRYON_SITE_ROOT', '/var/www/skryon.ru/html')),
    enabled=os.getenv('SKRYON_RELEASE_PUBLISH_ENABLED', '0').lower() in ('1','true','yes','on'),
)
_MUTEX = asyncio.Lock()
# Confirmation token expiry is intentionally memory-only: restart invalidates approvals.
_PENDING: dict[tuple[int, str], float] = {}


def _private_admin(message: Message) -> bool:
    return bool(message.from_user and is_admin(message.from_user.id) and
                message.chat.type == 'private')


def _private_callback(query: CallbackQuery) -> bool:
    return bool(query.from_user and is_admin(query.from_user.id) and query.message and
                query.message.chat.type == 'private')


def _keyboard(sha: str, *, ready: bool = False) -> InlineKeyboardMarkup:
    prefix = sha[:24]
    rows = [[InlineKeyboardButton(text='Проверить файлы', callback_data=f'release:check:{prefix}')]]
    if ready and _STORE.enabled:
        rows.append([InlineKeyboardButton(text='Выпустить обновление', callback_data=f'release:publish:{prefix}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _resolve_digest(prefix: str) -> str:
    if not re.fullmatch('[0-9a-f]{24}', prefix):
        raise ReleaseError('Invalid release token')
    matches = [entry.name for entry in _STORE.staging.glob(prefix + '*')
               if entry.is_dir() and re.fullmatch('[0-9a-f]{64}', entry.name)]
    if len(matches) != 1:
        raise ReleaseError('Release missing or ambiguous; upload manifest again')
    return matches[0]


async def release_help(message: Message) -> None:
    if not _private_admin(message):
        return
    await message.answer(
        'Релизы Skryon для Windows\n'
        '1. Пришли SkryonRelease-BUILD.json (подписанный на компьютере).\n'
        '2. Пришли все файлы .skpart из папки релиза.\n'
        '3. Нажми «Проверить файлы», затем «Выпустить обновление».\n'
        'Бот проверяет цифровую подпись манифеста, SHA-256 и версию установщика.\n'
        + ('Публикация включена.' if _STORE.enabled else
           'Публикация пока выключена на сервере. Загрузка и проверка доступны.'))


async def release_file(message: Message) -> None:
    if not _private_admin(message):
        return
    doc = message.document
    if not doc:
        return
    filename = doc.file_name or ''
    is_manifest = bool(MANIFEST_RE.fullmatch(filename))
    is_chunk = bool(CHUNK_RE.fullmatch(filename))
    if not is_manifest and not is_chunk:
        # The existing bot can ignore all unrelated files.
        return
    expected_size = int(doc.file_size or 0)
    if is_manifest and not (1 <= expected_size <= 12288):
        await message.answer('Манифест слишком большой или пустой.')
        return
    if is_chunk:
        try:
            _STORE.chunk_target(filename, expected_size)
        except ReleaseError as exc:
            await message.answer(f'Часть отклонена: {exc}')
            return
    async with _MUTEX:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix='skryon-tg-', dir=_STORE.staging,
                                             delete=False) as dest:
                temp_path = Path(dest.name)
            telegram_file = await message.bot.get_file(doc.file_id)
            await message.bot.download(telegram_file, destination=temp_path, timeout=120)
            if temp_path.stat().st_size != expected_size:
                raise ReleaseError('Telegram file size differs from declared size')
            if is_manifest:
                manifest = _STORE.stage_manifest(temp_path.read_bytes(), filename)
                await message.answer(
                    f'✅ Подписанный релиз {manifest.build}\n'
                    f'Версия {manifest.version} · Код {manifest.version_code}\n'
                    f'SHA256 {manifest.sha256[:12]}…\n'
                    'Пришли все части установщика.',
                    reply_markup=_keyboard(manifest.sha256))
            else:
                digest, index, total = _STORE.stage_chunk_file(filename, temp_path)
                temp_path = None
                try:
                    manifest, count, _ = _STORE.progress(digest)
                    await message.answer(
                        f'✅ Часть {index}/{total} · {manifest.build} · получено {count}/{total}',
                        reply_markup=_keyboard(digest, ready=(count == total)))
                except ReleaseError:
                    await message.answer(f'✅ Часть {index}/{total} принята. Пришли подписанный JSON.',
                                         reply_markup=_keyboard(digest))
        except (ReleaseError, OSError) as exc:
            log.warning('Release upload rejected (%s): %s', filename, exc)
            await message.answer(f'❌ Файл не принят: {exc}')
        except Exception:
            log.exception('Unexpected release upload failure')
            await message.answer('❌ Не удалось получить файл из Telegram. Действующий релиз не менялся.')
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


async def release_callback(query: CallbackQuery) -> None:
    if not _private_callback(query):
        await query.answer('Недостаточно прав', show_alert=True)
        return
    m = re.fullmatch(r'release:(check|publish|confirm|cancel):([0-9a-f]{24})', query.data or '')
    if not m:
        await query.answer('Устаревшая кнопка', show_alert=True)
        return
    action, short = m.groups()
    await query.answer()
    async with _MUTEX:
        try:
            digest = _resolve_digest(short)
            manifest, have, total = _STORE.progress(digest)
            if action == 'check':
                if not total or have != total:
                    await query.message.answer(f'{manifest.build}: получено {have}/{total} частей.')
                    return
                await asyncio.to_thread(_STORE._assembled_file, manifest)
                text = (f'✅ Сборка {manifest.build} проверена.\n'
                        f'Версия {manifest.version}, code {manifest.version_code}.\n'
                        'Подпись издателя, SHA256 и версия в EXE совпадают.')
                if not _STORE.enabled:
                    text += '\n⛔ Публикация на сервере отключена.'
                await query.message.answer(text, reply_markup=_keyboard(digest, ready=True))
            elif action == 'publish':
                if not _STORE.enabled:
                    raise ReleaseError('Публикация на сервере ещё не активирована')
                await asyncio.to_thread(_STORE.validate_candidate, digest)
                _PENDING[(query.from_user.id, digest)] = time.monotonic() + 900
                keys = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='Подтвердить публикацию', callback_data=f'release:confirm:{short}')],
                    [InlineKeyboardButton(text='Отмена', callback_data=f'release:cancel:{short}')]])
                await query.message.answer(
                    f'Опубликовать Skryon {manifest.build} ({manifest.version})?\n'
                    'После подтверждения пользователи смогут скачать новую версию.',
                    reply_markup=keys)
            elif action == 'cancel':
                _PENDING.pop((query.from_user.id, digest), None)
                await query.message.answer('Публикация отменена. Файлы остались в приватном хранилище.')
            elif action == 'confirm':
                expires = _PENDING.pop((query.from_user.id, digest), 0)
                if not expires or expires < time.monotonic():
                    raise ReleaseError('Подтверждение устарело, нажми «Выпустить обновление» ещё раз')
                final = await asyncio.to_thread(_STORE.publish, digest)
                log.warning('SKRYON_RELEASE_PUBLISHED build=%s version=%s code=%s sha=%s',
                            final.build, final.version, final.version_code, final.sha256)
                await query.message.answer(
                    f'🚀 Skryon {final.build} опубликован.\n'
                    f'{final.download_url}\n'
                    'Подписанный latest.json включён последним.')
        except (ReleaseError, OSError) as exc:
            await query.message.answer(f'❌ Публикация не выполнена: {exc}')
        except Exception:
            log.exception('Release callback failed')
            await query.message.answer('❌ Ошибка выпуска. Проверь журнал сервера; не повторяй публикацию вслепую.')


def register_release_routes(dp: Dispatcher) -> None:
    dp.message.register(release_help, Command('release'))
    dp.message.register(release_file, F.document)
    dp.callback_query.register(release_callback, F.data.startswith('release:'))
