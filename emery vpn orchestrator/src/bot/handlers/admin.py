import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import CallbackQuery, Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import (
    admin_key_card_keyboard,
    admin_keys_keyboard,
    admin_menu_keyboard,
    main_menu_keyboard,
)
from src.bot.utils.access import is_admin
from src.bot.utils.formatters import format_dt

logger = logging.getLogger(__name__)

router = Router(name="admin_menu")
client = BackendClient()
_PAGE_SIZE = 10


def _code_card_text(info: dict) -> str:
    return (
        "РРЅС„РѕСЂРјР°С†РёСЏ РїРѕ РєР»СЋС‡Сѓ:\n"
        f"ID: {info.get('id')}\n"
        f"Hash: <code>{(info.get('code_hash') or '')[:16]}</code>\n"
        f"РЎС‚Р°С‚СѓСЃ: {info.get('status')}\n"
        f"Telegram ID: {info.get('telegram_id')}\n"
        f"User ID: {info.get('user_id')}\n"
        f"Subscription ID: {info.get('subscription_id')}\n"
        f"РЎС‚Р°С‚СѓСЃ РїРѕРґРїРёСЃРєРё: {info.get('subscription_status')}\n"
        f"Р РµРіРёРѕРЅ: {info.get('region_code')}\n"
        f"РЎРѕР·РґР°РЅ: {format_dt(info.get('created_at'))}\n"
        f"РџРµСЂРІРѕРµ РёСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: {format_dt(info.get('first_redeemed_at'))}\n"
        f"Р”РµР№СЃС‚РІСѓРµС‚ РґРѕ: {format_dt(info.get('subscription_ends_at'))}"
    )


async def _show_admin_panel(message: Message) -> None:
    await message.answer(
        "РђРґРјРёРЅ-РїР°РЅРµР»СЊ\n\n"
        "РљРѕРјР°РЅРґС‹:\n"
        "- /keyinfo <РєРѕРґ> вЂ” РёРЅС„РѕСЂРјР°С†РёСЏ РїРѕ РєР»СЋС‡Сѓ\n"
        "- /keydelete <РєРѕРґ> вЂ” СѓРґР°Р»РёС‚СЊ (РґРµР°РєС‚РёРІРёСЂРѕРІР°С‚СЊ) РєР»СЋС‡",
        reply_markup=admin_menu_keyboard(),
    )


async def _render_keys_list(message: Message, page: int) -> None:
    offset = page * _PAGE_SIZE
    rows = await client.admin_list_codes(limit=_PAGE_SIZE, offset=offset)
    has_next = len(rows) == _PAGE_SIZE
    if not rows and page == 0:
        await message.edit_text("РљР»СЋС‡РµР№ РїРѕРєР° РЅРµС‚.", reply_markup=admin_menu_keyboard())
        return
    if not rows:
        await message.edit_text("Р­С‚Р° СЃС‚СЂР°РЅРёС†Р° РїСѓСЃС‚Р°.", reply_markup=admin_menu_keyboard())
        return
    lines = ["Р’СЃРµ РєР»СЋС‡Рё:"]
    for row in rows:
        lines.append(
            f"#{row.get('id')} | tg:{row.get('telegram_id')} | {row.get('status')} | РґРѕ {format_dt(row.get('subscription_ends_at'))}"
        )
    await message.edit_text(
        "\n".join(lines),
        reply_markup=admin_keys_keyboard(rows, page, has_next),
    )


@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ.")
        return
    await _show_admin_panel(message)


@router.message(Command("keyinfo"))
async def keyinfo_command_handler(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ.")
        return
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: /keyinfo ABCD1234EFGH")
        return
    try:
        info = await client.admin_code_info(code)
        await message.answer(_code_card_text(info), parse_mode="HTML")
    except BackendClientError as exc:
        logger.warning("admin keyinfo failed: err=%s", exc.detail)
        await message.answer(f"РћС€РёР±РєР° backend: {exc.detail}")


@router.message(Command("keydelete"))
async def keydelete_command_handler(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ.")
        return
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: /keydelete ABCD1234EFGH")
        return
    try:
        result = await client.admin_delete_code(code)
        action = "РґРµР°РєС‚РёРІРёСЂРѕРІР°РЅ" if result.get("deleted") else "СѓР¶Рµ РЅРµ Р°РєС‚РёРІРµРЅ"
        await message.answer(
            "РћРїРµСЂР°С†РёСЏ РІС‹РїРѕР»РЅРµРЅР°:\n"
            f"РЎС‚Р°С‚СѓСЃ: {result.get('status')}\n"
            f"Р РµР·СѓР»СЊС‚Р°С‚: {action}",
        )
    except BackendClientError as exc:
        logger.warning("admin keydelete failed: err=%s", exc.detail)
        await message.answer(f"РћС€РёР±РєР° backend: {exc.detail}")


@router.callback_query(F.data == "menu_admin")
async def admin_menu_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ.", show_alert=True)
        return
    await callback.message.edit_text(
        "РђРґРјРёРЅ-РїР°РЅРµР»СЊ",
        reply_markup=admin_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_"))
async def admin_callbacks_handler(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ.", show_alert=True)
        return
    data = callback.data or ""
    try:
        if data == "admin_stats":
            stats = await client.admin_stats()
            await callback.message.edit_text(
                "РЎС‚Р°С‚РёСЃС‚РёРєР°:\n"
                f"- РџРѕР»СЊР·РѕРІР°С‚РµР»Рё: {stats.get('users', 0)}\n"
                f"- РџРѕРґРїРёСЃРєРё: {stats.get('subscriptions', 0)}\n"
                f"- РђРєС‚РёРІРЅС‹Рµ СѓСЃС‚СЂРѕР№СЃС‚РІР°: {stats.get('active_devices', 0)}\n"
                f"- Р—Р°РєР°Р·С‹: {stats.get('orders', 0)}\n"
                f"- РћРїР»Р°С‚С‹: {stats.get('payments', 0)}\n"
                f"- РљРѕРґС‹: {stats.get('codes', 0)}",
                reply_markup=admin_menu_keyboard(),
            )
        elif data == "admin_nodes":
            nodes = await client.admin_nodes()
            if not nodes:
                text = "РЈР·Р»С‹ РЅРµ РЅР°Р№РґРµРЅС‹."
            else:
                lines = ["РЎРїРёСЃРѕРє СѓР·Р»РѕРІ:"]
                for n in nodes[:20]:
                    lines.append(f"- #{n['id']} {n['name']} [{n['region_code']}] {n['status']} {n['endpoint']}")
                text = "\n".join(lines)
            await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
        elif data == "admin_grant_self":
            grant = await client.admin_grant_subscription(callback.from_user.id, 1)
            await callback.message.edit_text(
                "Р‘РµСЃРїР»Р°С‚РЅР°СЏ РїРѕРґРїРёСЃРєР° РїСЂРѕРґР»РµРЅР° РЅР° 1 РјРµСЃСЏС†.\n"
                f"Р”РµР№СЃС‚РІСѓРµС‚ РґРѕ: {format_dt(grant.get('ends_at'))}",
                reply_markup=admin_menu_keyboard(),
            )
        elif data == "admin_code_self":
            code = await client.admin_generate_code(callback.from_user.id)
            await callback.message.edit_text(
                "РљРѕРґ СЃРіРµРЅРµСЂРёСЂРѕРІР°РЅ.\n"
                f"<code>{code.get('activation_code')}</code>\n"
                "РџРѕРєР°Р·С‹РІР°РµС‚СЃСЏ РѕРґРёРЅ СЂР°Р· РїРѕР»РЅРѕСЃС‚СЊСЋ.",
                parse_mode="HTML",
                reply_markup=admin_menu_keyboard(),
            )
        elif data == "admin_problem_activations":
            rows = await client.admin_problem_activations()
            if not rows:
                text = "РџСЂРѕР±Р»РµРјРЅС‹С… Р°РєС‚РёРІР°С†РёР№ РЅРµС‚."
            else:
                lines = ["РџСЂРѕР±Р»РµРјРЅС‹Рµ Р°РєС‚РёРІР°С†РёРё:"]
                for row in rows[:20]:
                    lines.append(
                        f"- {format_dt(row.get('created_at'))} | {row.get('action')} | actor={row.get('actor_id')}"
                    )
                text = "\n".join(lines)
            await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
        elif data.startswith("admin_keys_"):
            page = int(data.split("_")[2])
            await _render_keys_list(callback.message, page)
        elif data.startswith("admin_key_generate_"):
            _, _, _, code_id, page = data.split("_")
            result = await client.admin_generate_code_for_key(int(code_id))
            await callback.message.edit_text(
                "РќРѕРІС‹Р№ РєРѕРґ РґР»СЏ СЌС‚РѕР№ РїРѕРґРїРёСЃРєРё:\n"
                f"<code>{result.get('activation_code')}</code>",
                parse_mode="HTML",
                reply_markup=admin_key_card_keyboard(int(code_id), int(page)),
            )
        elif data.startswith("admin_key_delete_"):
            _, _, _, code_id, page = data.split("_")
            result = await client.admin_delete_code_by_id(int(code_id))
            await callback.message.edit_text(
                "РљР»СЋС‡ РѕР±СЂР°Р±РѕС‚Р°РЅ:\n"
                f"ID: {result.get('id')}\n"
                f"РЎС‚Р°С‚СѓСЃ: {result.get('status')}\n"
                f"РЈРґР°Р»С‘РЅ: {'РґР°' if result.get('deleted') else 'РЅРµС‚'}",
                reply_markup=admin_key_card_keyboard(int(code_id), int(page)),
            )
        elif data.startswith("admin_key_"):
            _, _, code_id, page = data.split("_")
            info = await client.admin_get_code(int(code_id))
            await callback.message.edit_text(
                _code_card_text(info),
                parse_mode="HTML",
                reply_markup=admin_key_card_keyboard(int(code_id), int(page)),
            )
        else:
            await callback.message.edit_text("РќРµРёР·РІРµСЃС‚РЅР°СЏ Р°РґРјРёРЅ-РєРѕРјР°РЅРґР°.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
    except BackendClientError as exc:
        logger.warning("admin backend call failed: action=%s err=%s", data, exc.detail)
        await callback.message.edit_text(
            f"РћС€РёР±РєР° backend: {exc.detail}",
            reply_markup=admin_menu_keyboard(),
        )
        await callback.answer()
