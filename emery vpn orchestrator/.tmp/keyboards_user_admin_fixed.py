from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.common.config import settings


def user_reply_keyboard(include_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Купить подписку"), KeyboardButton(text="Мои подписки")],
        [KeyboardButton(text="Получить VPN-конфиг")],
        [KeyboardButton(text="Мои устройства"), KeyboardButton(text="Мои коды")],
        [KeyboardButton(text="Помощь"), KeyboardButton(text="Поддержка")],
        [KeyboardButton(text="Канал")],
    ]
    if include_admin:
        rows.append([KeyboardButton(text="👑 Админ")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )


def links_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Поддержка", url=settings.support_url)],
            [InlineKeyboardButton(text="Канал", url=settings.channel_url)],
        ]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Купить подписку", callback_data="menu_buy")
    kb.button(text="Мои подписки", callback_data="menu_my_sub")
    kb.button(text="Получить VPN-конфиг", callback_data="menu_vpn_config")
    kb.button(text="Мои устройства", callback_data="menu_my_devices")
    kb.button(text="Мои коды", callback_data="menu_my_codes")
    kb.button(text="Помощь", callback_data="menu_help")
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


def pay_keyboard(order_id: int, plan_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я оплатил", callback_data=f"pay_{order_id}_{plan_code}")],
            [InlineKeyboardButton(text="Назад в меню", callback_data="menu_back")],
        ]
    )


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Статистика", callback_data="admin_stats")
    kb.button(text="Узлы", callback_data="admin_nodes")
    kb.button(text="Ключи", callback_data="admin_codes")
    kb.button(text="Поиск", callback_data="admin_codes_search")
    kb.button(text="+1 месяц", callback_data="admin_grant_self")
    kb.button(text="Код себе", callback_data="admin_code_self")
    kb.button(text="Проблемы", callback_data="admin_problem_activations")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def admin_codes_keyboard(items: list[dict], *, total: int, page: int, page_size: int, mode: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    current = items[0] if items else None
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="←", callback_data=f"admin_codes_page:{mode}:{page - 1}"))
    if current:
        row.append(InlineKeyboardButton(text=f"Открыть #{current['id']}", callback_data=f"admin_code_open:{current['id']}:{mode}:{page}"))
    if (page + 1) * page_size < total:
        row.append(InlineKeyboardButton(text="→", callback_data=f"admin_codes_page:{mode}:{page + 1}"))
    if row:
        kb.row(*row)
    kb.row(
        InlineKeyboardButton(text="Поиск", callback_data="admin_codes_search"),
        InlineKeyboardButton(text="В меню", callback_data="admin_back"),
    )
    return kb.as_markup()


def admin_code_detail_keyboard(code_id: int, status: str, *, mode: str, page: int) -> InlineKeyboardMarkup:
    status_normalized = (status or "").lower()
    kb = InlineKeyboardBuilder()
    if status_normalized == "active":
        kb.button(text="Деактивировать", callback_data=f"admin_code_action:revoke:{code_id}:{mode}:{page}")
    else:
        kb.button(text="Активировать", callback_data=f"admin_code_action:activate:{code_id}:{mode}:{page}")
    kb.button(text="Удалить", callback_data=f"admin_code_action:delete:{code_id}:{mode}:{page}")
    kb.button(text="← К списку", callback_data=f"admin_codes_back:{mode}:{page}")
    kb.button(text="В меню", callback_data="admin_back")
    kb.adjust(1)
    return kb.as_markup()
