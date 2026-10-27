"""Windows releases inside the existing private Telegram administration panel."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.ui.keyboards import admin_menu_keyboard
from src.bot.utils.access import is_admin
from src.bot.utils import windows_release_store as store
from src.bot.utils import windows_upload_chunks as chunks

router = Router(name="windows_releases")
_upload_lock = asyncio.Lock()
_publish_lock = asyncio.Lock()


def _authorized(from_id: int | None, chat_type: str) -> bool:
    return from_id is not None and is_admin(from_id) and chat_type == ChatType.PRIVATE


def _screen(text: str, actions: list[tuple[str, str]]) -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardBuilder()
    for label, data in actions:
        kb.button(text=label, callback_data=data)
    kb.button(text="← Админ-панель", callback_data="wr:back")
    kb.adjust(1)
    return text, kb.as_markup()


def _status() -> tuple[str, InlineKeyboardMarkup]:
    data = store.state()
    current = data.get("current")
    latest = (f"{current['version']} (код {current['versionCode']})"
              if current else "ещё не опубликовано ботом")
    staged = data.get("staged", {})
    issues = store.publishing_readiness()
    text = (
        "📦 Skryon · Windows-релизы\n\n"
        f"На сайте: {latest}\n"
        f"Ожидают проверки: {len(staged)}\n"
        f"Минимальная версия: {data.get('minimum', store.BASE_CODE)} "
        "(справочно, не управляет действующим API)\n\n"
        + ("✅ Публикация настроена." if not issues else
           "🔒 Публикация заблокирована до настройки безопасности:\n"
           + "\n".join("• " + x for x in issues))
        + "\n\nДля больших установщиков пришлите все части .skpart "
          "по 18 МиБ прямо в этот чат. Для небольших EXE можно прислать один файл."
    )
    return _screen(text, [
        ("📥 Загрузить Windows-сборку", "wr:upload"),
        ("🗑 Сбросить незавершённые части", "wr:cancel_uploads"),
        ("📋 Ожидают публикации", "wr:pending"),
        ("🗂 История выпусков", "wr:history"),
        ("🔐 Правила принудительного обновления", "wr:mandatory"),
        ("🔎 Диагностика выпуска", "wr:status"),
    ])


def _pending() -> tuple[str, InlineKeyboardMarkup]:
    s = store.state().get("staged", {})
    kb = InlineKeyboardBuilder()
    for digest, row in sorted(s.items(), key=lambda kv: kv[1]["uploadedAt"], reverse=True)[:10]:
        kb.button(text=f"{row['version']} · {'✅' if row['authenticodeValid'] else '⚠️'}",
                  callback_data=f"wr:details:{digest[:12]}")
    kb.button(text="← Windows-релизы", callback_data="wr:menu")
    kb.adjust(1)
    return ("📋 Загруженные сборки\n\n"
            "✅ Подпись установщика подтверждена\n"
            "⚠️ Нет подтверждённой подписи Windows"
            if s else "Нет ожидающих сборок."), kb.as_markup()


def _find(prefix: str) -> tuple[str, dict]:
    staged = store.state().get("staged", {})
    matches = [(key, row) for key, row in staged.items() if key.startswith(prefix)]
    if len(matches) != 1:
        raise store.ReleaseError("Сборка не найдена или неоднозначный идентификатор.")
    return matches[0]


@router.message(Command("release", "releases", "windows"))
async def release_command(message: Message) -> None:
    if not _authorized(message.from_user.id if message.from_user else None, message.chat.type):
        await message.answer("Админ-функции доступны только в личном чате владельца.")
        return
    text, keyboard = _status()
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "admin_releases")
@router.callback_query(F.data == "wr:menu")
async def release_menu(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type): 
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    try:
        text, keyboard = _status()
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        text, keyboard = _status()
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "wr:back")
async def release_back(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await callback.message.edit_text("👑 Админ-панель Skryon", reply_markup=admin_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "wr:upload")
async def release_upload(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await callback.message.edit_text(
        "📥 Загрузка Windows через обычный Telegram\n\n"
        "Большой EXE делится на части .skpart по 18 МиБ. "
        "Отправьте все части как документы в этот личный чат, "
        "можно несколько сразу, в произвольном порядке.\n\n"
        "Бот сам соберёт EXE, проверит SHA-256, прочитает версию "
        "и предложит предварительный просмотр. Ничего не публикуется "
        "до подтверждения.\n\n"
        "Для маленького .exe (до 18 МиБ) отправьте файл целиком. "
        "API ID и API Hash НЕ нужны.",
        reply_markup=_screen("", [("← Релизы", "wr:menu")])[1],
    )
    await callback.answer()


@router.message(F.document)
async def release_document(message: Message, bot: Bot) -> None:
    if not _authorized(message.from_user.id if message.from_user else None, message.chat.type):
        return
    doc = message.document
    if not doc:
        return
    filename = doc.file_name or ""
    is_chunk = filename.endswith(".skpart")
    if not is_chunk and not filename.lower().endswith(".exe"):
        return
    if is_chunk:
        try:
            chunks.parse(filename)
        except store.ReleaseError as exc:
            await message.answer("⛔ " + str(exc))
            return
    if not doc.file_size or doc.file_size > chunks.PART_BYTES:
        await message.answer(
            "⛔ Обычный Telegram API скачивает не больше 20 МБ. "
            "Разбейте установщик на части по 18 МиБ через "
            "Skryon Prepare Telegram Release. Затем отправьте файлы .skpart.")
        return
    # Queue document updates instead of rejecting busy updates. Multiple
    # documents are delivered concurrently by Telegram and aiogram.
    async with _upload_lock:
        notice = await message.answer(
            f"📥 Принимаю {'часть сборки' if is_chunk else 'Windows EXE'}…")
        temp = None
        try:
            folder = store.release_root() / "incoming"
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(dir=folder, prefix="tg-", suffix=".part",
                                             delete=False) as f:
                temp = Path(f.name)
            remote_file = await bot.get_file(doc.file_id)
            await bot.download(remote_file, destination=str(temp), timeout=240)
            if temp.stat().st_size != doc.file_size:
                raise store.ReleaseError("Файл получен не полностью.")
            if is_chunk:
                received, total, row = await asyncio.to_thread(
                    chunks.add_part, temp, filename, doc.file_size,
                    message.from_user.id)
                temp = None
                if row is None:
                    await notice.edit_text(
                        f"✅ Часть сохранена. Готово: {received}/{total}. "
                        "Отправьте оставшиеся .skpart. "
                        "Данные сохраняются даже после перезапуска бота.")
                    return
            else:
                row = await asyncio.to_thread(
                    store.stage, temp, filename, message.from_user.id)
                temp = None
            digest = row["sha256"][:12]
            text = (
                "✅ Windows-сборка получена и проверена, но ЕЩЁ НЕ ОПУБЛИКОВАНА.\n\n"
                f"Версия: {row['version']}\n"
                f"Код протокола (предполагаемый): {row['versionCode']}\n"
                f"Размер: {row['size']/1048576:.1f} МиБ\n"
                f"SHA-256: {row['sha256']}\n"
                f"Подпись Windows: {row['authenticodeReason']}\n\n"
                "Версия из EXE не доказывает соответствие кода протокола. "
                "Перед публикацией нужна подлинная подпись и проверенный выпуск.")
            _, keyboard = _screen("", [
                ("🔍 Посмотреть выпуск", f"wr:details:{digest}"),
                ("🗑 Удалить загрузку", f"wr:drop:{digest}"),
                ("← Релизы", "wr:menu"),
            ])
            await notice.edit_text(text, reply_markup=keyboard)
        except Exception as exc:
            from aiogram.exceptions import TelegramBadRequest
            detail = (str(exc)[:170] if isinstance(exc, store.ReleaseError)
                      else "Не удалось получить или проверить документ.")
            if isinstance(exc, TelegramBadRequest):
                detail = ("Telegram не выдал файл боту. "
                          "Отправьте части .skpart по 18 МиБ.")
            await notice.edit_text("⛔ " + detail)
        finally:
            if temp: temp.unlink(missing_ok=True)


@router.callback_query(F.data == "wr:cancel_uploads")
async def cancel_uploads(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    async with _upload_lock:
        removed = await asyncio.to_thread(chunks.cancel_parts, callback.from_user.id)
    await callback.message.edit_text(
        f"🗑 Незавершённых загрузок удалено: {removed}. "
        "Уже подготовленные кандидаты не тронуты.",
        reply_markup=_screen("", [("← Релизы", "wr:menu")])[1])
    await callback.answer()


@router.callback_query(F.data == "wr:pending")
async def pending(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    text, keyboard = _pending()
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("wr:details:"))
async def details(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    try:
        digest, row = _find(callback.data.rsplit(":", 1)[1])
    except store.ReleaseError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    text = (f"📦 Кандидат {row['version']}\n\n"
            f"Код: {row['versionCode']}\n"
            f"Размер: {row['size']/1048576:.1f} МБ\n"
            f"SHA-256: {digest}\n"
            f"Подпись: {row['authenticodeReason']}\n\n"
            "Публикация требует действительного сертификата Windows, "
            "совпадающего ключа подписи манифестов и защищённого SFTP.")
    _, kb = _screen(text, [
        ("✅ Опубликовать", f"wr:approve:{digest[:12]}"),
        ("🗑 Удалить", f"wr:drop:{digest[:12]}"),
        ("← Список", "wr:pending"),
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("wr:approve:"))
async def approve(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    try:
        digest, row = _find(callback.data.rsplit(":", 1)[1])
    except store.ReleaseError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    _, kb = _screen("", [
        (f"⚠️ Да, опубликовать {row['version']}", f"wr:confirm:{digest[:12]}"),
        ("Отмена", f"wr:details:{digest[:12]}"),
    ])
    await callback.message.edit_text(
        f"Подтвердите публикацию Windows {row['version']}.\n"
        "После проверки будет изменён указатель latest.json на сайте. "
        "Минимальную разрешённую версию API это НЕ меняет.",
        reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("wr:confirm:"))
async def confirm(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    if _publish_lock.locked():
        await callback.answer("Уже выполняется публикация.", show_alert=True)
        return
    async with _publish_lock:
        try:
            digest, row = _find(callback.data.rsplit(":", 1)[1])
            await callback.answer("Проверяю и загружаю выпуск…")
            done = await store.publish(digest, callback.from_user.id)
            await callback.message.edit_text(
                f"✅ Опубликован Windows {done['version']}.\n"
                "Пользователи получат подписанный список обновлений. "
                "Принудительное обновление не включено.",
                reply_markup=_screen("", [("← Релизы", "wr:menu")])[1])
        except store.ReleaseError as exc:
            await callback.message.edit_text(
                "⛔ Безопасная публикация запрещена:\n" + str(exc),
                reply_markup=_screen("", [("← Релизы", "wr:menu")])[1])
        except Exception:
            await callback.message.edit_text(
                "⛔ Публикация не подтверждена. Проверьте сайт и состояние загрузок; "
                "не повторяйте её вслепую.",
                reply_markup=_screen("", [("← Релизы", "wr:menu")])[1])


@router.callback_query(F.data.startswith("wr:drop:"))
async def drop(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    try:
        digest, row = _find(callback.data.rsplit(":", 1)[1])
        store.discard(digest, callback.from_user.id)
        await callback.message.edit_text("🗑 Тестовая сборка удалена. На сайте ничего не менялось.",
            reply_markup=_screen("", [("← Релизы", "wr:menu")])[1])
    except store.ReleaseError as exc:
        await callback.message.edit_text("Ошибка: " + str(exc))
    await callback.answer()


@router.callback_query(F.data == "wr:history")
async def history(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    rows = store.state().get("published", [])[-12:]
    lines = [f"• {r['version']} · {r['versionCode']} · {r['sha256'][:12]}"
             for r in reversed(rows)]
    await callback.message.edit_text(
        "🗂 История публикаций бота\n\n" + ("\n".join(lines) if lines else "Пока нет."),
        reply_markup=_screen("", [("← Релизы", "wr:menu")])[1])
    await callback.answer()


@router.callback_query(F.data == "wr:mandatory")
async def mandatory(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await callback.message.edit_text(
        "🔒 Принудительное обновление\n\n"
        "Нельзя повышать минимальную версию до проверки, что новая сборка "
        "действительно доступна пользователям и поддерживает подписанный API.\n\n"
        "В этом выпуске бота изменение серверного минимума намеренно "
        "заблокировано, пока не подключён безопасный серверный API управления "
        "с аудитом и откатом. Загрузить файл ≠ отключить пользователей.",
        reply_markup=_screen("", [("← Релизы", "wr:menu")])[1])
    await callback.answer()


@router.callback_query(F.data == "wr:status")
async def status(callback: CallbackQuery) -> None:
    if not _authorized(callback.from_user.id, callback.message.chat.type):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    text, keyboard = _status()
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()
