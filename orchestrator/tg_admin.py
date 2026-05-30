import asyncio
from urllib.parse import unquote, urlparse

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from config import BOT_TOKEN, is_admin
from storage import create_activation_code, get_codes, init_storage, revoke_activation_code, save_server


def server_name_from_config(config_text: str) -> str:
    parsed = urlparse(config_text.strip())
    if parsed.fragment:
        return unquote(parsed.fragment)
    return 'Skryon-Server'


async def require_admin(message: Message) -> bool:
    user = message.from_user
    if not user or not is_admin(user.id):
        await message.answer('Нет доступа')
        return False
    return True


async def start_cmd(message: Message) -> None:
    if not await require_admin(message):
        return
    await message.answer('/newcode 30 note\n/codes\n/revoke CODE\n/addconfig VLESS_LINK')


async def newcode_cmd(message: Message) -> None:
    if not await require_admin(message):
        return
    parts = (message.text or '').split(maxsplit=2)
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
    note = parts[2] if len(parts) > 2 else ''
    code = create_activation_code(days, note)
    await message.answer('Код создан:\n' + code)


async def codes_cmd(message: Message) -> None:
    if not await require_admin(message):
        return
    rows = get_codes(20)
    if not rows:
        await message.answer('Кодов пока нет')
        return
    await message.answer('\n'.join(str(row) for row in rows))


async def revoke_cmd(message: Message) -> None:
    if not await require_admin(message):
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2:
        await message.answer('Формат: /revoke CODE')
        return
    ok = revoke_activation_code(parts[1])
    await message.answer('Отключён' if ok else 'Не найден')


async def addconfig_cmd(message: Message) -> None:
    if not await require_admin(message):
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
    save_server(name, 'AUTO', config_text)
    await message.answer('Конфиг сохранён: ' + name)


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError('BOT_TOKEN is empty')
    init_storage()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.message.register(start_cmd, Command('start'))
    dp.message.register(newcode_cmd, Command('newcode'))
    dp.message.register(codes_cmd, Command('codes'))
    dp.message.register(revoke_cmd, Command('revoke'))
    dp.message.register(addconfig_cmd, Command('addconfig'))
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
