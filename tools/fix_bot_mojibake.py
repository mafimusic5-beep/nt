from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]

START_PY = ROOT / "emery vpn orchestrator/src/bot/handlers/start.py"
KEYBOARDS_PY = ROOT / "emery vpn orchestrator/src/bot/ui/keyboards.py"
ADMIN_PY = ROOT / "emery vpn orchestrator/src/bot/handlers/admin.py"

START_CONTENT = dedent('''\
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
        f"Добро пожаловать в {settings.brand_name}.\n"
        "Выберите действие в меню ниже.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.callback_query(F.data == "menu_back")
async def menu_back_handler(callback: CallbackQuery) -> None:
    renew_context.pop(callback.from_user.id, None)
    await callback.message.edit_text("Главное меню", reply_markup=main_menu_keyboard(callback.from_user.id))
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


@router.callback_query(F.data == "menu_extend_key")
async def extend_key_menu_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Как продлить подписку по ключу?",
        reply_markup=renew_mode_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renewmode_"))
async def renew_mode_handler(callback: CallbackQuery) -> None:
    mode = callback.data.split("_", 1)[1]
    await callback.message.edit_text(
        "Выберите тариф продления.",
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
        "Отправьте ключ активации сообщением.\n"
        "Можно вставить не только свой ключ.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"menu_help"}))
async def help_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Помощь:\n"
        "1) Выберите «Купить подписку» или «Продлить по ключу».\n"
        "2) Оплатите счет и нажмите «Я оплатил».\n"
        "3) Для продления по ключу сначала отправьте сам ключ сообщением.",
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
            f"Не удалось получить VPN-конфиг: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    import_text = (cfg.get("import_text") or "").strip()
    error = cfg.get("error")
    if error or not import_text:
        await callback.message.edit_text(
            f"VPN-конфиг пока недоступен: {error or 'empty_config'}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "Ваш VPN-конфиг:\n"
        f"<code>{import_text}</code>",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer("Конфиг готов")


@router.callback_query(F.data == "menu_my_sub")
async def my_subscription_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        status = await client.get_subscription_status(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(
            f"Не удалось получить подписку: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not status.get("active"):
        await callback.message.edit_text("Активной подписки нет.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
        return
    await callback.message.edit_text(
        "Моя подписка:\n"
        f"- Тариф: {plan_name(status.get('plan_code'))}\n"
        f"- Действует до: {format_dt(status.get('ends_at'))}\n"
        f"- Устройства: {status.get('devices_used', 0)}/{status.get('devices_limit', 5)}",
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
            f"Не удалось получить список устройств: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not devices:
        await callback.message.edit_text("Активных устройств пока нет.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
        return
    lines = ["Мои устройства:"]
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
            f"Не удалось получить коды: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not codes:
        await callback.message.edit_text("Кодов пока нет.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
        return
    lines = ["Мои коды (история):"]
    for idx, code in enumerate(codes[:20], start=1):
        lines.append(
            f"{idx}) Статус: {code.get('status')}\n"
            f"   Создан: {format_dt(code.get('created_at'))}\n"
            f"   Первое использование: {format_dt(code.get('first_redeemed_at'))}"
        )
    await callback.message.edit_text("\n".join(lines), reply_markup=main_menu_keyboard(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "menu_admin")
async def admin_shortcut_handler(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.message.edit_text("Админ-панель", reply_markup=admin_menu_keyboard())
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
            reply_markup=main_menu_keyboard(callback.from_user.id),
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


@router.message()
async def renew_key_input_handler(message: Message) -> None:
    ctx = renew_context.get(message.from_user.id)
    if not ctx:
        return
    text = (message.text or "").strip().upper()
    if not text or text.startswith("/"):
        await message.answer("Отправьте ключ обычным сообщением.")
        return
    if not ACTIVATION_CODE_RE.match(text):
        await message.answer("Некорректный формат ключа. Отправьте ключ ещё раз.")
        return

    plan_code = str(ctx.get("plan_code"))
    issue_new_code = bool(ctx.get("issue_new_code"))
    try:
        order = await client.create_order(message.from_user.id, plan_code)
    except BackendClientError as exc:
        logger.warning("renew order creation failed: tg=%s plan=%s err=%s", message.from_user.id, plan_code, exc.detail)
        await message.answer(
            f"Не удалось создать заказ: {exc.detail}",
            reply_markup=main_menu_keyboard(message.from_user.id),
        )
        renew_context.pop(message.from_user.id, None)
        return

    renew_context.pop(message.from_user.id, None)
    amount = order.get("amount_rub", 0)
    result_line = "После оплаты будет выпущен новый ключ для этой подписки." if issue_new_code else "После оплаты этот же ключ будет продлён."
    await message.answer(
        "Счёт сформирован для продления по ключу.\n"
        f"Тариф: {plan_name(plan_code)}\n"
        f"Сумма: {amount} руб\n"
        f"Ключ: <code>{text}</code>\n"
        f"{result_line}\n"
        "После оплаты нажмите «Я оплатил».",
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
        await callback.answer("Некорректные данные платежа.", show_alert=True)
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
            f"Не удалось подтвердить оплату: {exc.detail}\nПопробуйте позже.",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    code = result.get("activation_code", "—")
    ends_at = format_dt(after.get("ends_at"))
    before_dt = parse_dt(before.get("ends_at"))
    after_dt = parse_dt(after.get("ends_at"))
    extended = bool(before_dt and after_dt and after_dt > before_dt)
    if target_code and issue_new_code:
        extension_line = "Подписка по ключу продлена. Выпущен новый ключ."
    elif target_code:
        extension_line = "Подписка по ключу продлена. Старый ключ сохранён."
    else:
        extension_line = "Срок подписки продлён." if extended else "Подписка активирована."
    await callback.message.edit_text(
        "Оплата подтверждена.\n"
        f"{extension_line}\n\n"
        f"Ключ (показывается один раз):\n<code>{code}</code>\n\n"
        f"Тариф: {plan_name(plan_code)}\n"
        f"Действует до: {ends_at}",
        reply_markup=main_menu_keyboard(callback.from_user.id),
        parse_mode="HTML",
    )
    await callback.answer("Готово")
''')

KEYBOARDS_CONTENT = dedent('''\
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.common.config import settings


def main_menu_keyboard(telegram_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Купить подписку", callback_data="menu_buy")
    kb.button(text="Продлить по ключу", callback_data="menu_extend_key")
    kb.button(text="Мои подписки", callback_data="menu_my_sub")
    kb.button(text="Получить VPN-конфиг", callback_data="menu_vpn_config")
    kb.button(text="Мои устройства", callback_data="menu_my_devices")
    kb.button(text="Мои коды", callback_data="menu_my_codes")
    kb.button(text="Помощь", callback_data="menu_help")
    if telegram_id in settings.admin_id_list:
        kb.button(text="Админ", callback_data="menu_admin")
    kb.button(text="Поддержка", url=settings.support_url)
    kb.button(text="Канал", url=settings.channel_url)
    kb.adjust(1)
    return kb.as_markup()


def plans_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="1 месяц — 600 руб", callback_data="buy_warmup_1m")
    kb.button(text="3 месяца — 1500 руб", callback_data="buy_warmup_3m")
    kb.button(text="6 месяцев — 2700 руб", callback_data="buy_warmup_6m")
    kb.button(text="12 месяцев — 4800 руб", callback_data="buy_warmup_12m")
    kb.button(text="Назад в меню", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()


def renew_mode_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Продлить этот же ключ", callback_data="renewmode_keep")
    kb.button(text="Продлить и выдать новый ключ", callback_data="renewmode_new")
    kb.button(text="Назад в меню", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()


def renew_plans_keyboard(mode: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="1 месяц — 600 руб", callback_data=f"renewplan_{mode}_warmup_1m")
    kb.button(text="3 месяца — 1500 руб", callback_data=f"renewplan_{mode}_warmup_3m")
    kb.button(text="6 месяцев — 2700 руб", callback_data=f"renewplan_{mode}_warmup_6m")
    kb.button(text="12 месяцев — 4800 руб", callback_data=f"renewplan_{mode}_warmup_12m")
    kb.button(text="Назад в меню", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()


def pay_keyboard(order_id: int, plan_code: str, target_code: str | None = None, issue_new_code: bool = False) -> InlineKeyboardMarkup:
    if target_code:
        mode = "new" if issue_new_code else "keep"
        callback_data = f"pay|{order_id}|{plan_code}|renew|{mode}|{target_code.upper()}"
    else:
        callback_data = f"pay|{order_id}|{plan_code}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я оплатил", callback_data=callback_data)],
            [InlineKeyboardButton(text="Назад в меню", callback_data="menu_back")],
        ]
    )


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Все ключи", callback_data="admin_keys_0")
    kb.button(text="Статистика", callback_data="admin_stats")
    kb.button(text="Список узлов", callback_data="admin_nodes")
    kb.button(text="Выдать себе +1 месяц", callback_data="admin_grant_self")
    kb.button(text="Сгенерировать код себе", callback_data="admin_code_self")
    kb.button(text="Проблемные активации", callback_data="admin_problem_activations")
    kb.button(text="Назад в меню", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()


def admin_keys_keyboard(items: list[dict], page: int, has_next: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for item in items:
        short_hash = (item.get("code_hash") or "")[:8]
        status = item.get("status") or "-"
        tg = item.get("telegram_id") or "-"
        kb.button(text=f"#{item['id']} tg:{tg} {status} {short_hash}", callback_data=f"admin_key_{item['id']}_{page}")
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_keys_{page - 1}"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_keys_{page + 1}"))
    if nav_row:
        kb.row(*nav_row)
    kb.row(InlineKeyboardButton(text="Назад", callback_data="menu_admin"))
    kb.adjust(1)
    return kb.as_markup()


def admin_key_card_keyboard(code_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Удалить", callback_data=f"admin_key_delete_{code_id}_{page}")],
            [InlineKeyboardButton(text="Сгенерировать новый код", callback_data=f"admin_key_generate_{code_id}_{page}")],
            [InlineKeyboardButton(text="Назад к списку", callback_data=f"admin_keys_{page}")],
        ]
    )
''')

ADMIN_CONTENT = dedent('''\
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
        "Информация по ключу:\n"
        f"ID: {info.get('id')}\n"
        f"Hash: <code>{(info.get('code_hash') or '')[:16]}</code>\n"
        f"Статус: {info.get('status')}\n"
        f"Telegram ID: {info.get('telegram_id')}\n"
        f"User ID: {info.get('user_id')}\n"
        f"Subscription ID: {info.get('subscription_id')}\n"
        f"Статус подписки: {info.get('subscription_status')}\n"
        f"Регион: {info.get('region_code')}\n"
        f"Создан: {format_dt(info.get('created_at'))}\n"
        f"Первое использование: {format_dt(info.get('first_redeemed_at'))}\n"
        f"Действует до: {format_dt(info.get('subscription_ends_at'))}"
    )


async def _show_admin_panel(message: Message) -> None:
    await message.answer(
        "Админ-панель\n\n"
        "Команды:\n"
        "- /keyinfo <код> — информация по ключу\n"
        "- /keydelete <код> — удалить (деактивировать) ключ",
        reply_markup=admin_menu_keyboard(),
    )


async def _render_keys_list(message: Message, page: int) -> None:
    offset = page * _PAGE_SIZE
    rows = await client.admin_list_codes(limit=_PAGE_SIZE, offset=offset)
    has_next = len(rows) == _PAGE_SIZE
    if not rows and page == 0:
        await message.edit_text("Ключей пока нет.", reply_markup=admin_menu_keyboard())
        return
    if not rows:
        await message.edit_text("Эта страница пуста.", reply_markup=admin_menu_keyboard())
        return
    lines = ["Все ключи:"]
    for row in rows:
        lines.append(
            f"#{row.get('id')} | tg:{row.get('telegram_id')} | {row.get('status')} | до {format_dt(row.get('subscription_ends_at'))}"
        )
    await message.edit_text(
        "\n".join(lines),
        reply_markup=admin_keys_keyboard(rows, page, has_next),
    )


@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    await _show_admin_panel(message)


@router.message(Command("keyinfo"))
async def keyinfo_command_handler(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("Использование: /keyinfo ABCD1234EFGH")
        return
    try:
        info = await client.admin_code_info(code)
        await message.answer(_code_card_text(info), parse_mode="HTML")
    except BackendClientError as exc:
        logger.warning("admin keyinfo failed: err=%s", exc.detail)
        await message.answer(f"Ошибка backend: {exc.detail}")


@router.message(Command("keydelete"))
async def keydelete_command_handler(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("Использование: /keydelete ABCD1234EFGH")
        return
    try:
        result = await client.admin_delete_code(code)
        action = "деактивирован" if result.get("deleted") else "уже не активен"
        await message.answer(
            "Операция выполнена:\n"
            f"Статус: {result.get('status')}\n"
            f"Результат: {action}",
        )
    except BackendClientError as exc:
        logger.warning("admin keydelete failed: err=%s", exc.detail)
        await message.answer(f"Ошибка backend: {exc.detail}")


@router.callback_query(F.data == "menu_admin")
async def admin_menu_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.message.edit_text(
        "Админ-панель",
        reply_markup=admin_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_"))
async def admin_callbacks_handler(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    data = callback.data or ""
    try:
        if data == "admin_stats":
            stats = await client.admin_stats()
            await callback.message.edit_text(
                "Статистика:\n"
                f"- Пользователи: {stats.get('users', 0)}\n"
                f"- Подписки: {stats.get('subscriptions', 0)}\n"
                f"- Активные устройства: {stats.get('active_devices', 0)}\n"
                f"- Заказы: {stats.get('orders', 0)}\n"
                f"- Оплаты: {stats.get('payments', 0)}\n"
                f"- Коды: {stats.get('codes', 0)}",
                reply_markup=admin_menu_keyboard(),
            )
        elif data == "admin_nodes":
            nodes = await client.admin_nodes()
            if not nodes:
                text = "Узлы не найдены."
            else:
                lines = ["Список узлов:"]
                for n in nodes[:20]:
                    lines.append(f"- #{n['id']} {n['name']} [{n['region_code']}] {n['status']} {n['endpoint']}")
                text = "\n".join(lines)
            await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
        elif data == "admin_grant_self":
            grant = await client.admin_grant_subscription(callback.from_user.id, 1)
            await callback.message.edit_text(
                "Бесплатная подписка продлена на 1 месяц.\n"
                f"Действует до: {format_dt(grant.get('ends_at'))}",
                reply_markup=admin_menu_keyboard(),
            )
        elif data == "admin_code_self":
            code = await client.admin_generate_code(callback.from_user.id)
            await callback.message.edit_text(
                "Код сгенерирован.\n"
                f"<code>{code.get('activation_code')}</code>\n"
                "Показывается один раз полностью.",
                parse_mode="HTML",
                reply_markup=admin_menu_keyboard(),
            )
        elif data == "admin_problem_activations":
            rows = await client.admin_problem_activations()
            if not rows:
                text = "Проблемных активаций нет."
            else:
                lines = ["Проблемные активации:"]
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
                "Новый код для этой подписки:\n"
                f"<code>{result.get('activation_code')}</code>",
                parse_mode="HTML",
                reply_markup=admin_key_card_keyboard(int(code_id), int(page)),
            )
        elif data.startswith("admin_key_delete_"):
            _, _, _, code_id, page = data.split("_")
            result = await client.admin_delete_code_by_id(int(code_id))
            await callback.message.edit_text(
                "Ключ обработан:\n"
                f"ID: {result.get('id')}\n"
                f"Статус: {result.get('status')}\n"
                f"Удалён: {'да' if result.get('deleted') else 'нет'}",
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
            await callback.message.edit_text("Неизвестная админ-команда.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
    except BackendClientError as exc:
        logger.warning("admin backend call failed: action=%s err=%s", data, exc.detail)
        await callback.message.edit_text(
            f"Ошибка backend: {exc.detail}",
            reply_markup=admin_menu_keyboard(),
        )
        await callback.answer()
''')

for path, content in [
    (START_PY, START_CONTENT),
    (KEYBOARDS_PY, KEYBOARDS_CONTENT),
    (ADMIN_PY, ADMIN_CONTENT),
]:
    path.write_text(content, encoding="utf-8")
    print(f"fixed {path}")
