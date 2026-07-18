import asyncio
import logging
from urllib.parse import unquote, urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from config import BOT_TOKEN, is_admin
from server_pool_sync import ServerPoolSyncError, is_enabled as pool_sync_enabled, publish_server, unpublish_server
from storage import (
    create_activation_code,
    delete_server,
    get_codes,
    get_server,
    init_storage,
    list_events,
    list_server_records,
    list_servers,
    revoke_activation_code,
    save_server,
    set_active_server,
    set_server_pool_node_id,
)

logger = logging.getLogger(__name__)


def server_name_from_config(config_text: str) -> str:
    parsed = urlparse(config_text.strip())
    if parsed.fragment:
        return unquote(parsed.fragment)
    return 'Skryon-Server'


def is_admin_message(message: Message) -> bool:
    user = message.from_user
    return bool(user and is_admin(user.id))


async def ignore_non_admin(message: Message) -> None:
    return None


def server_line(row: dict) -> str:
    marker = '[active]' if row.get('is_active') else '[saved]'
    pool = ' | pool #' + str(row['pool_node_id']) if row.get('pool_node_id') else ''
    return marker + ' ' + str(row['id']) + ' | ' + row['name'] + ' | ' + row['region'] + pool


def event_line(row: dict) -> str:
    code = row.get('code') or '-'
    plan = row.get('plan') or '-'
    return str(row['id']) + ' | ' + row['event_type'] + ' | ' + code + ' | ' + plan + ' | ' + row['created_at']


async def start_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    await message.answer('/newcode 30 note\n/codes\n/revoke CODE\n/addconfig VLESS_LINK\n/configs\n/useconfig ID\n/delconfig ID\n/syncconfigs\n/events')


async def newcode_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    parts = (message.text or '').split(maxsplit=2)
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
    note = parts[2] if len(parts) > 2 else ''
    code = create_activation_code(days, note)
    await message.answer('Код создан:\n' + code)


async def codes_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    rows = get_codes(20)
    if not rows:
        await message.answer('Кодов пока нет')
        return
    await message.answer('\n'.join(str(row) for row in rows))


async def revoke_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2:
        await message.answer('Формат: /revoke CODE')
        return
    ok = revoke_activation_code(parts[1])
    await message.answer('Отключён' if ok else 'Не найден')


async def addconfig_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2:
        await message.answer('Формат: /addconfig VLESS_LINK')
        return
    config_text = parts[1].strip()
    if not config_text.startswith('vless://'):
        await message.answer('Нужна полная ссылка vless://')
        return
    if not pool_sync_enabled():
        await message.answer(
            'Конфиг не добавлен: синхронизация старого пула не настроена. '
            'Проверь доступ к Emery backend.'
        )
        return
    name = server_name_from_config(config_text)
    server_id = save_server(name, 'AUTO', config_text)
    server = get_server(server_id)
    try:
        pool_node_id = await publish_server(server or {})
        set_server_pool_node_id(server_id, pool_node_id)
    except ServerPoolSyncError as exc:
        delete_server(server_id)
        await message.answer(
            'Конфиг не добавлен: старый пул не синхронизировался ('
            + str(exc)
            + '). Локальное изменение отменено.'
        )
        return
    await message.answer(
        'Конфиг сохранён во всех пулах и выбран активным:\n'
        + str(server_id)
        + ' | '
        + name
        + ' | pool #'
        + str(pool_node_id)
    )


async def configs_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    rows = list_servers(20)
    if not rows:
        await message.answer('Конфигов пока нет')
        return
    await message.answer('\n'.join(server_line(row) for row in rows))


async def useconfig_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Формат: /useconfig ID')
        return
    ok = set_active_server(int(parts[1]))
    await message.answer('Активный конфиг выбран' if ok else 'Конфиг не найден')


async def delconfig_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Формат: /delconfig ID')
        return
    server_id = int(parts[1])
    server = get_server(server_id)
    if not server:
        await message.answer('Конфиг не найден')
        return

    if not pool_sync_enabled():
        await message.answer(
            'Удаление отменено: синхронизация старого пула не настроена. '
            'Конфиг оставлен, чтобы версии приложения не разошлись. '
            'Проверь доступ к Emery backend.'
        )
        return
    try:
        await unpublish_server(server)
    except ServerPoolSyncError as exc:
        await message.answer(
            'Удаление отменено: старый пул не ответил ('
            + str(exc)
            + '). Конфиг оставлен, чтобы версии приложения не разошлись.'
        )
        return

    ok = delete_server(server_id)
    if not ok:
        await message.answer('Конфиг не найден')
        return
    await message.answer('Конфиг удалён из синхронизированных приложений')


async def syncconfigs_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    if not pool_sync_enabled():
        await message.answer('Синхронизация не настроена: проверь доступ к Emery backend.')
        return

    synced, errors = await sync_pool_configs()

    text = 'Старый пул синхронизирован: ' + str(synced) + ' конфиг(ов).'
    if errors:
        text += '\nОшибки:\n' + '\n'.join(errors[:10])
    await message.answer(text)


async def sync_pool_configs() -> tuple[int, list[str]]:
    synced = 0
    errors: list[str] = []
    for server in list_server_records():
        try:
            pool_node_id = await publish_server(server)
            set_server_pool_node_id(int(server['id']), pool_node_id)
            synced += 1
        except ServerPoolSyncError as exc:
            errors.append('#' + str(server['id']) + ': ' + str(exc))
    return synced, errors


async def events_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    rows = list_events(20)
    if not rows:
        await message.answer('Событий пока нет')
        return
    await message.answer('\n'.join(event_line(row) for row in rows))


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError('BOT_TOKEN is empty')
    init_storage()
    if pool_sync_enabled():
        synced, errors = await sync_pool_configs()
        logger.info('Initial server-pool sync completed: synced=%s errors=%s', synced, len(errors))
        for error in errors[:10]:
            logger.warning('Initial server-pool sync failed: %s', error)
    else:
        logger.warning('Server-pool sync is disabled because the Emery admin key is unavailable')
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    admin_filter = F.from_user.id.func(is_admin)
    dp.message.register(start_cmd, admin_filter, Command('start'))
    dp.message.register(newcode_cmd, admin_filter, Command('newcode'))
    dp.message.register(codes_cmd, admin_filter, Command('codes'))
    dp.message.register(revoke_cmd, admin_filter, Command('revoke'))
    dp.message.register(addconfig_cmd, admin_filter, Command('addconfig'))
    dp.message.register(configs_cmd, admin_filter, Command('configs'))
    dp.message.register(useconfig_cmd, admin_filter, Command('useconfig'))
    dp.message.register(delconfig_cmd, admin_filter, Command('delconfig'))
    dp.message.register(syncconfigs_cmd, admin_filter, Command('syncconfigs'))
    dp.message.register(events_cmd, admin_filter, Command('events'))
    dp.message.register(ignore_non_admin)

    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
