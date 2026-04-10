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
