import asyncio
from urllib.parse import unquote, urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from config import BOT_TOKEN, is_admin
from storage import create_activation_code, delete_server, get_codes, init_storage, list_events, list_servers, revoke_activation_code, save_server, set_active_server


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
    return marker + ' ' + str(row['id']) + ' | ' + row['name'] + ' | ' + row['region']


def event_line(row: dict) -> str:
    code = row.get('code') or '-'
    plan = row.get('plan') or '-'
    return str(row['id']) + ' | ' + row['event_type'] + ' | ' + code + ' | ' + plan + ' | ' + row['created_at']


async def start_cmd(message: Message) -> None:
    if not is_admin_message(message):
        return
    await message.answer('/newcode 30 note\n/codes\n/revoke CODE\n/addconfig VLESS_LINK\n/configs\n/useconfig ID\n/delconfig ID\n/events')


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
    name = server_name_from_config(config_text)
    server_id = save_server(name, 'AUTO', config_text)
    await message.answer('Конфиг сохранён и выбран активным:\n' + str(server_id) + ' | ' + name)


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
    ok = delete_server(int(parts[1]))
    await message.answer('Конфиг удалён' if ok else 'Конфиг не найден')


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
    dp.message.register(events_cmd, admin_filter, Command('events'))
    dp.message.register(ignore_non_admin)

    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
