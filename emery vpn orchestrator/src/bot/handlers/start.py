import logging
import re
import secrets
from uuid import uuid4

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import (
    admin_menu_keyboard,
    main_menu_keyboard,
    pay_keyboard,
    plans_keyboard,
    renew_mode_keyboard,
    renew_plans_keyboard,
)
from src.bot.utils.access import is_admin
from src.bot.utils.formatters import format_dt, parse_dt, plan_name
from src.common.config import settings

logger = logging.getLogger(__name__)

router = Router(name="user_menu")
client = BackendClient()
renew_context: dict[int, dict[str, object]] = {}
ACTIVATION_CODE_RE = re.compile(r"^[A-Z0-9]{8,32}$")


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        f"Р”РѕР±СЂРѕ РїРѕР¶Р°Р»РѕРІР°С‚СЊ РІ {settings.brand_name}.\n"
        "Р’С‹Р±РµСЂРёС‚Рµ РґРµР№СЃС‚РІРёРµ РІ РјРµРЅСЋ РЅРёР¶Рµ.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.callback_query(F.data == "menu_back")
async def menu_back_handler(callback: CallbackQuery) -> None:
    renew_context.pop(callback.from_user.id, None)
    await callback.message.edit_text("Р“Р»Р°РІРЅРѕРµ РјРµРЅСЋ", reply_markup=main_menu_keyboard(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "menu_buy")
async def buy_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Р’С‹Р±РµСЂРёС‚Рµ С‚Р°СЂРёС„ РґР»СЏ РїСЂРѕРґСѓРєС‚Р° В«РџСЂРѕРіСЂРµРІВ».",
        reply_markup=plans_keyboard(),
    )
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "menu_extend_key")
async def extend_key_menu_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "РљР°Рє РїСЂРѕРґР»РёС‚СЊ РїРѕРґРїРёСЃРєСѓ РїРѕ РєР»СЋС‡Сѓ?",
        reply_markup=renew_mode_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renewmode_"))
async def renew_mode_handler(callback: CallbackQuery) -> None:
    mode = callback.data.split("_", 1)[1]
    await callback.message.edit_text(
        "Р’С‹Р±РµСЂРёС‚Рµ С‚Р°СЂРёС„ РїСЂРѕРґР»РµРЅРёСЏ.",
        reply_markup=renew_plans_keyboard(mode),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renewplan_"))
async def renew_plan_handler(callback: CallbackQuery) -> None:
    _, mode, prefix, suffix = callback.data.split("_", 3)
    plan_code = f"{prefix}_{suffix}"
    renew_context[callback.from_user.id] = {
        "plan_code": plan_code,
        "issue_new_code": mode == "new",
    }
    await callback.message.edit_text(
        "РћС‚РїСЂР°РІСЊС‚Рµ РєР»СЋС‡ Р°РєС‚РёРІР°С†РёРё СЃРѕРѕР±С‰РµРЅРёРµРј.\n"
        "РњРѕР¶РЅРѕ РІСЃС‚Р°РІРёС‚СЊ РЅРµ С‚РѕР»СЊРєРѕ СЃРІРѕР№ РєР»СЋС‡.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"menu_help"}))
async def help_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "РџРѕРјРѕС‰СЊ:\n"
        "1) Р’С‹Р±РµСЂРёС‚Рµ В«РљСѓРїРёС‚СЊ РїРѕРґРїРёСЃРєСѓВ» РёР»Рё В«РџСЂРѕРґР»РёС‚СЊ РїРѕ РєР»СЋС‡СѓВ».\n"
        "2) РћРїР»Р°С‚РёС‚Рµ СЃС‡РµС‚ Рё РЅР°Р¶РјРёС‚Рµ В«РЇ РѕРїР»Р°С‚РёР»В».\n"
        "3) Р”Р»СЏ РїСЂРѕРґР»РµРЅРёСЏ РїРѕ РєР»СЋС‡Сѓ СЃРЅР°С‡Р°Р»Р° РѕС‚РїСЂР°РІСЊС‚Рµ СЃР°Рј РєР»СЋС‡ СЃРѕРѕР±С‰РµРЅРёРµРј.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "menu_vpn_config")
async def vpn_config_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        cfg = await client.get_vpn_config(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(
            f"РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ VPN-РєРѕРЅС„РёРі: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    import_text = (cfg.get("import_text") or "").strip()
    error = cfg.get("error")
    if error or not import_text:
        await callback.message.edit_text(
            f"VPN-РєРѕРЅС„РёРі РїРѕРєР° РЅРµРґРѕСЃС‚СѓРїРµРЅ: {error or 'empty_config'}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "Р’Р°С€ VPN-РєРѕРЅС„РёРі:\n"
        f"<code>{import_text}</code>",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer("РљРѕРЅС„РёРі РіРѕС‚РѕРІ")


@router.callback_query(F.data == "menu_my_sub")
async def my_subscription_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        status = await client.get_subscription_status(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(
            f"РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ РїРѕРґРїРёСЃРєСѓ: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not status.get("active"):
        await callback.message.edit_text("РђРєС‚РёРІРЅРѕР№ РїРѕРґРїРёСЃРєРё РЅРµС‚.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
        return
    await callback.message.edit_text(
        "РњРѕСЏ РїРѕРґРїРёСЃРєР°:\n"
        f"- РўР°СЂРёС„: {plan_name(status.get('plan_code'))}\n"
        f"- Р”РµР№СЃС‚РІСѓРµС‚ РґРѕ: {format_dt(status.get('ends_at'))}\n"
        f"- РЈСЃС‚СЂРѕР№СЃС‚РІР°: {status.get('devices_used', 0)}/{status.get('devices_limit', 5)}",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "menu_my_devices")
async def my_devices_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        devices = await client.get_user_devices(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(
            f"РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ СЃРїРёСЃРѕРє СѓСЃС‚СЂРѕР№СЃС‚РІ: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not devices:
        await callback.message.edit_text("РђРєС‚РёРІРЅС‹С… СѓСЃС‚СЂРѕР№СЃС‚РІ РїРѕРєР° РЅРµС‚.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
        return
    lines = ["РњРѕРё СѓСЃС‚СЂРѕР№СЃС‚РІР°:"]
    for idx, dev in enumerate(devices[:20], start=1):
        lines.append(
            f"{idx}) {dev.get('platform', 'android')} | {dev.get('device_name', '-') or '-'}\n"
            f"   fp: {dev.get('device_fingerprint', '')[:10]}...\n"
            f"   heartbeat: {format_dt(dev.get('last_seen_at'))}"
        )
    await callback.message.edit_text("\n".join(lines), reply_markup=main_menu_keyboard(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "menu_my_codes")
async def my_codes_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        codes = await client.get_user_codes(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(
            f"РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ РєРѕРґС‹: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not codes:
        await callback.message.edit_text("РљРѕРґРѕРІ РїРѕРєР° РЅРµС‚.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
        return
    lines = ["РњРѕРё РєРѕРґС‹ (РёСЃС‚РѕСЂРёСЏ):"]
    for idx, code in enumerate(codes[:20], start=1):
        lines.append(
            f"{idx}) РЎС‚Р°С‚СѓСЃ: {code.get('status')}\n"
            f"   РЎРѕР·РґР°РЅ: {format_dt(code.get('created_at'))}\n"
            f"   РџРµСЂРІРѕРµ РёСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: {format_dt(code.get('first_redeemed_at'))}"
        )
    await callback.message.edit_text("\n".join(lines), reply_markup=main_menu_keyboard(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "menu_admin")
async def admin_shortcut_handler(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ.", show_alert=True)
        return
    await callback.message.edit_text("РђРґРјРёРЅ-РїР°РЅРµР»СЊ", reply_markup=admin_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("buy_warmup_"))
async def plan_selected_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    plan_code = callback.data.replace("buy_", "", 1)
    try:
        await client.get_subscription_status(telegram_id)
        order = await client.create_order(telegram_id, plan_code)
    except BackendClientError as exc:
        logger.warning("order creation failed: tg=%s plan=%s err=%s", telegram_id, plan_code, exc.detail)
        await callback.message.edit_text(
            f"РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕР·РґР°С‚СЊ Р·Р°РєР°Р·: {exc.detail}\nРџСЂРѕРІРµСЂСЊС‚Рµ РЅР°СЃС‚СЂРѕР№РєРё Рё РїРѕРїСЂРѕР±СѓР№С‚Рµ РїРѕР·Р¶Рµ.",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    amount = order.get("amount_rub", 0)
    await callback.message.edit_text(
        "РЎС‡С‘С‚ СЃС„РѕСЂРјРёСЂРѕРІР°РЅ.\n"
        f"РўР°СЂРёС„: {plan_name(plan_code)}\n"
        f"РЎСѓРјРјР°: {amount} СЂСѓР±\n"
        "РџРѕСЃР»Рµ РѕРїР»Р°С‚С‹ РЅР°Р¶РјРёС‚Рµ В«РЇ РѕРїР»Р°С‚РёР»В».",
        reply_markup=pay_keyboard(order["order_id"], plan_code),
    )
    await callback.answer()


@router.message()
async def renew_key_input_handler(message: Message) -> None:
    ctx = renew_context.get(message.from_user.id)
    if not ctx:
        return
    text = (message.text or "").strip().upper()
    if not text or text.startswith("/"):
        await message.answer("РћС‚РїСЂР°РІСЊС‚Рµ РєР»СЋС‡ РѕР±С‹С‡РЅС‹Рј СЃРѕРѕР±С‰РµРЅРёРµРј.")
        return
    if not ACTIVATION_CODE_RE.match(text):
        await message.answer("РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚ РєР»СЋС‡Р°. РћС‚РїСЂР°РІСЊС‚Рµ РєР»СЋС‡ РµС‰С‘ СЂР°Р·.")
        return

    plan_code = str(ctx.get("plan_code"))
    issue_new_code = bool(ctx.get("issue_new_code"))
    try:
        order = await client.create_order(message.from_user.id, plan_code)
    except BackendClientError as exc:
        logger.warning("renew order creation failed: tg=%s plan=%s err=%s", message.from_user.id, plan_code, exc.detail)
        await message.answer(
            f"РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕР·РґР°С‚СЊ Р·Р°РєР°Р·: {exc.detail}",
            reply_markup=main_menu_keyboard(message.from_user.id),
        )
        renew_context.pop(message.from_user.id, None)
        return

    renew_context.pop(message.from_user.id, None)
    amount = order.get("amount_rub", 0)
    result_line = "РџРѕСЃР»Рµ РѕРїР»Р°С‚С‹ Р±СѓРґРµС‚ РІС‹РїСѓС‰РµРЅ РЅРѕРІС‹Р№ РєР»СЋС‡ РґР»СЏ СЌС‚РѕР№ РїРѕРґРїРёСЃРєРё." if issue_new_code else "РџРѕСЃР»Рµ РѕРїР»Р°С‚С‹ СЌС‚РѕС‚ Р¶Рµ РєР»СЋС‡ Р±СѓРґРµС‚ РїСЂРѕРґР»С‘РЅ."
    await message.answer(
        "РЎС‡С‘С‚ СЃС„РѕСЂРјРёСЂРѕРІР°РЅ РґР»СЏ РїСЂРѕРґР»РµРЅРёСЏ РїРѕ РєР»СЋС‡Сѓ.\n"
        f"РўР°СЂРёС„: {plan_name(plan_code)}\n"
        f"РЎСѓРјРјР°: {amount} СЂСѓР±\n"
        f"РљР»СЋС‡: <code>{text}</code>\n"
        f"{result_line}\n"
        "РџРѕСЃР»Рµ РѕРїР»Р°С‚С‹ РЅР°Р¶РјРёС‚Рµ В«РЇ РѕРїР»Р°С‚РёР»В».",
        parse_mode="HTML",
        reply_markup=pay_keyboard(order["order_id"], plan_code, target_code=text, issue_new_code=issue_new_code),
    )


@router.callback_query(F.data.startswith("pay|") | F.data.startswith("pay_"))
async def payment_confirm_handler(callback: CallbackQuery) -> None:
    data = callback.data or ""
    target_code = None
    issue_new_code = False
    try:
        if data.startswith("pay|"):
            parts = data.split("|")
            order_id = int(parts[1])
            plan_code = parts[2]
            if len(parts) == 6 and parts[3] == "renew":
                issue_new_code = parts[4] == "new"
                target_code = parts[5].upper()
        else:
            _, order_id_str, plan_code = data.split("_", 2)
            order_id = int(order_id_str)
    except (ValueError, IndexError):
        await callback.answer("РќРµРєРѕСЂСЂРµРєС‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїР»Р°С‚РµР¶Р°.", show_alert=True)
        return

    telegram_id = callback.from_user.id
    idempotency_key = f"tg_{telegram_id}_{uuid4().hex}"
    provider_payment_id = f"sim_{secrets.token_hex(6)}"

    try:
        before = await client.get_subscription_status(telegram_id)
        result = await client.confirm_payment(
            order_id,
            provider_payment_id,
            idempotency_key,
            target_code=target_code,
            issue_new_code=issue_new_code,
        )
        after = await client.get_subscription_status(telegram_id)
    except BackendClientError as exc:
        logger.warning("payment confirm failed: tg=%s order=%s err=%s", telegram_id, order_id, exc.detail)
        await callback.message.edit_text(
            f"РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕРґС‚РІРµСЂРґРёС‚СЊ РѕРїР»Р°С‚Сѓ: {exc.detail}\nРџРѕРїСЂРѕР±СѓР№С‚Рµ РїРѕР·Р¶Рµ.",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    code = result.get("activation_code", "вЂ”")
    ends_at = format_dt(after.get("ends_at"))
    before_dt = parse_dt(before.get("ends_at"))
    after_dt = parse_dt(after.get("ends_at"))
    extended = bool(before_dt and after_dt and after_dt > before_dt)
    if target_code and issue_new_code:
        extension_line = "РџРѕРґРїРёСЃРєР° РїРѕ РєР»СЋС‡Сѓ РїСЂРѕРґР»РµРЅР°. Р’С‹РїСѓС‰РµРЅ РЅРѕРІС‹Р№ РєР»СЋС‡."
    elif target_code:
        extension_line = "РџРѕРґРїРёСЃРєР° РїРѕ РєР»СЋС‡Сѓ РїСЂРѕРґР»РµРЅР°. РЎС‚Р°СЂС‹Р№ РєР»СЋС‡ СЃРѕС…СЂР°РЅС‘РЅ."
    else:
        extension_line = "РЎСЂРѕРє РїРѕРґРїРёСЃРєРё РїСЂРѕРґР»С‘РЅ." if extended else "РџРѕРґРїРёСЃРєР° Р°РєС‚РёРІРёСЂРѕРІР°РЅР°."
    await callback.message.edit_text(
        "РћРїР»Р°С‚Р° РїРѕРґС‚РІРµСЂР¶РґРµРЅР°.\n"
        f"{extension_line}\n\n"
        f"РљР»СЋС‡ (РїРѕРєР°Р·С‹РІР°РµС‚СЃСЏ РѕРґРёРЅ СЂР°Р·):\n<code>{code}</code>\n\n"
        f"РўР°СЂРёС„: {plan_name(plan_code)}\n"
        f"Р”РµР№СЃС‚РІСѓРµС‚ РґРѕ: {ends_at}",
        reply_markup=main_menu_keyboard(callback.from_user.id),
        parse_mode="HTML",
    )
    await callback.answer("Р“РѕС‚РѕРІРѕ")
