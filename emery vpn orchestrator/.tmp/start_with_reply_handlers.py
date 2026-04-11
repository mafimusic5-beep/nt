import logging
import secrets
from uuid import uuid4

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import links_keyboard, main_menu_keyboard, pay_keyboard, plans_keyboard, user_reply_keyboard
from src.bot.utils.access import is_admin
from src.bot.utils.formatters import format_dt, parse_dt, plan_name
from src.common.config import settings

logger = logging.getLogger(__name__)
router = Router(name="user_menu")
client = BackendClient()
USER_MENU_TEXTS = {
    "Купить подписку",
    "Мои подписки",
    "Получить VPN-конфиг",
    "Мои устройства",
    "Мои коды",
    "Помощь",
    "Поддержка",
    "Канал",
}


async def _show_user_panel(message: Message) -> None:
    await message.answer(
        f"Добро пожаловать в {settings.brand_name}.\nВыберите действие в меню ниже.",
        reply_markup=user_reply_keyboard(include_admin=is_admin(message.from_user.id)),
    )
    await message.answer("Главное меню", reply_markup=main_menu_keyboard())


async def _show_subscription_message(message: Message, telegram_id: int) -> None:
    try:
        status = await client.get_subscription_status(telegram_id)
    except BackendClientError as exc:
        await message.answer(f"Не удалось получить подписку: {exc.detail}", reply_markup=main_menu_keyboard())
        return
    if not status.get("active"):
        await message.answer("Активной подписки нет.", reply_markup=main_menu_keyboard())
        return
    await message.answer(
        "Моя подписка:\n"
        f"- Тариф: {plan_name(status.get('plan_code'))}\n"
        f"- Действует до: {format_dt(status.get('ends_at'))}\n"
        f"- Устройства: {status.get('devices_used', 0)}/{status.get('devices_limit', 5)}",
        reply_markup=main_menu_keyboard(),
    )


async def _show_devices_message(message: Message, telegram_id: int) -> None:
    try:
        devices = await client.get_user_devices(telegram_id)
    except BackendClientError as exc:
        await message.answer(f"Не удалось получить список устройств: {exc.detail}", reply_markup=main_menu_keyboard())
        return
    if not devices:
        await message.answer("Активных устройств пока нет.", reply_markup=main_menu_keyboard())
        return
    lines = ["Мои устройства:"]
    for idx, dev in enumerate(devices[:20], start=1):
        lines.append(
            f"{idx}) {dev.get('platform', 'android')} | {dev.get('device_name', '-') or '-'}\n"
            f"   fp: {dev.get('device_fingerprint', '')[:10]}...\n"
            f"   heartbeat: {format_dt(dev.get('last_seen_at'))}"
        )
    await message.answer("\n".join(lines), reply_markup=main_menu_keyboard())


async def _show_codes_message(message: Message, telegram_id: int) -> None:
    try:
        codes = await client.get_user_codes(telegram_id)
    except BackendClientError as exc:
        await message.answer(f"Не удалось получить коды: {exc.detail}", reply_markup=main_menu_keyboard())
        return
    if not codes:
        await message.answer("Кодов пока нет.", reply_markup=main_menu_keyboard())
        return
    lines = ["Мои коды (история):"]
    for idx, code in enumerate(codes[:20], start=1):
        lines.append(
            f"{idx}) Статус: {code.get('status')}\n"
            f"   Создан: {format_dt(code.get('created_at'))}\n"
            f"   Первое использование: {format_dt(code.get('first_redeemed_at'))}"
        )
    await message.answer("\n".join(lines), reply_markup=main_menu_keyboard())


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await _show_user_panel(message)


@router.message(F.text.in_(USER_MENU_TEXTS))
async def reply_menu_handler(message: Message) -> None:
    text = message.text or ""
    if text == "Купить подписку":
        await message.answer("Выберите тариф для продукта «Прогрев».", reply_markup=plans_keyboard())
    elif text == "Мои подписки":
        await _show_subscription_message(message, message.from_user.id)
    elif text == "Получить VPN-конфиг":
        telegram_id = message.from_user.id
        try:
            cfg = await client.get_vpn_config(telegram_id)
        except BackendClientError as exc:
            await message.answer(f"Не удалось получить VPN-конфиг: {exc.detail}", reply_markup=main_menu_keyboard())
            return
        import_text = (cfg.get("import_text") or "").strip()
        error = cfg.get("error")
        if error or not import_text:
            await message.answer(
                f"VPN-конфиг пока недоступен: {error or 'empty_config'}",
                reply_markup=main_menu_keyboard(),
            )
            return
        await message.answer(
            "Ваш VPN-конфиг:\n"
            f"<code>{import_text}</code>",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    elif text == "Мои устройства":
        await _show_devices_message(message, message.from_user.id)
    elif text == "Мои коды":
        await _show_codes_message(message, message.from_user.id)
    elif text == "Помощь":
        await message.answer(
            "Помощь:\n"
            "1) Выберите «Купить подписку».\n"
            "2) Оплатите счет и нажмите «Я оплатил».\n"
            "3) Сохраните код активации — он показывается один раз полностью.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await message.answer("Ссылки:", reply_markup=links_keyboard())


@router.callback_query(F.data == "menu_back")
async def menu_back_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Главное меню", reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu_buy")
async def buy_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Выберите тариф для продукта «Прогрев».",
        reply_markup=plans_keyboard(),
    )
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "menu_help")
async def help_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Помощь:\n"
        "1) Выберите «Купить подписку».\n"
        "2) Оплатите счет и нажмите «Я оплатил».\n"
        "3) Сохраните код активации — он показывается один раз полностью.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu_vpn_config")
async def vpn_config_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        cfg = await client.get_vpn_config(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(
            f"Не удалось получить VPN-конфиг: {exc.detail}",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return
    import_text = (cfg.get("import_text") or "").strip()
    error = cfg.get("error")
    if error or not import_text:
        await callback.message.edit_text(
            f"VPN-конфиг пока недоступен: {error or 'empty_config'}",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "Ваш VPN-конфиг:\n"
        f"<code>{import_text}</code>",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer("Конфиг готов")


@router.callback_query(F.data == "menu_my_sub")
async def my_subscription_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        status = await client.get_subscription_status(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(f"Не удалось получить подписку: {exc.detail}", reply_markup=main_menu_keyboard())
        await callback.answer()
        return
    if not status.get("active"):
        await callback.message.edit_text("Активной подписки нет.", reply_markup=main_menu_keyboard())
        await callback.answer()
        return
    await callback.message.edit_text(
        "Моя подписка:\n"
        f"- Тариф: {plan_name(status.get('plan_code'))}\n"
        f"- Действует до: {format_dt(status.get('ends_at'))}\n"
        f"- Устройства: {status.get('devices_used', 0)}/{status.get('devices_limit', 5)}",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu_my_devices")
async def my_devices_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        devices = await client.get_user_devices(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(f"Не удалось получить список устройств: {exc.detail}", reply_markup=main_menu_keyboard())
        await callback.answer()
        return
    if not devices:
        await callback.message.edit_text("Активных устройств пока нет.", reply_markup=main_menu_keyboard())
        await callback.answer()
        return
    lines = ["Мои устройства:"]
    for idx, dev in enumerate(devices[:20], start=1):
        lines.append(
            f"{idx}) {dev.get('platform', 'android')} | {dev.get('device_name', '-') or '-'}\n"
            f"   fp: {dev.get('device_fingerprint', '')[:10]}...\n"
            f"   heartbeat: {format_dt(dev.get('last_seen_at'))}"
        )
    await callback.message.edit_text("\n".join(lines), reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu_my_codes")
async def my_codes_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        codes = await client.get_user_codes(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(f"Не удалось получить коды: {exc.detail}", reply_markup=main_menu_keyboard())
        await callback.answer()
        return
    if not codes:
        await callback.message.edit_text("Кодов пока нет.", reply_markup=main_menu_keyboard())
        await callback.answer()
        return
    lines = ["Мои коды (история):"]
    for idx, code in enumerate(codes[:20], start=1):
        lines.append(
            f"{idx}) Статус: {code.get('status')}\n"
            f"   Создан: {format_dt(code.get('created_at'))}\n"
            f"   Первое использование: {format_dt(code.get('first_redeemed_at'))}"
        )
    await callback.message.edit_text("\n".join(lines), reply_markup=main_menu_keyboard())
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
            f"Не удалось создать заказ: {exc.detail}\nПроверьте настройки и попробуйте позже.",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return
    amount = order.get("amount_rub", 0)
    await callback.message.edit_text(
        "Счёт сформирован.\n"
        f"Тариф: {plan_name(plan_code)}\n"
        f"Сумма: {amount} руб\n"
        "После оплаты нажмите «Я оплатил».",
        reply_markup=pay_keyboard(order["order_id"], plan_code),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_"))
async def payment_confirm_handler(callback: CallbackQuery) -> None:
    try:
        _, order_id_str, plan_code = callback.data.split("_", 2)
        order_id = int(order_id_str)
    except ValueError:
        await callback.answer("Некорректные данные платежа.", show_alert=True)
        return
    telegram_id = callback.from_user.id
    idempotency_key = f"tg_{telegram_id}_{uuid4().hex}"
    provider_payment_id = f"sim_{secrets.token_hex(6)}"
    try:
        before = await client.get_subscription_status(telegram_id)
        result = await client.confirm_payment(order_id, provider_payment_id, idempotency_key)
        after = await client.get_subscription_status(telegram_id)
    except BackendClientError as exc:
        logger.warning("payment confirm failed: tg=%s order=%s err=%s", telegram_id, order_id, exc.detail)
        await callback.message.edit_text(
            f"Не удалось подтвердить оплату: {exc.detail}\nПопробуйте позже.",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return
    code = result.get("activation_code", "—")
    ends_at = format_dt(after.get("ends_at"))
    before_dt = parse_dt(before.get("ends_at"))
    after_dt = parse_dt(after.get("ends_at"))
    extended = bool(before_dt and after_dt and after_dt > before_dt)
    extension_line = "Срок подписки продлён." if extended else "Подписка активирована."
    await callback.message.edit_text(
        "Оплата подтверждена.\n"
        f"{extension_line}\n\n"
        f"Ваш код активации (показывается один раз):\n<code>{code}</code>\n\n"
        f"Тариф: {plan_name(plan_code)}\n"
        f"Действует до: {ends_at}",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer("Готово")
