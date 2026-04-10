from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from src.common.config import settings

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
    kb.button(text="Список узлов", callback_data="admin_nodes")
    kb.button(text="Все ключи", callback_data="admin_codes")
    kb.button(text="Поиск ключей", callback_data="admin_codes_search")
    kb.button(text="Выдать себе +1 месяц", callback_data="admin_grant_self")
    kb.button(text="Сгенерировать код себе", callback_data="admin_code_self")
    kb.button(text="Проблемные активации", callback_data="admin_problem_activations")
    kb.adjust(1)
    return kb.as_markup()

def admin_codes_keyboard(items: list[dict], *, total: int, page: int, page_size: int, mode: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for item in items:
        kb.button(text=f"#{item['id']} • tg {item.get('telegram_id') or '—'} • {item.get('status', '—')}", callback_data=f"admin_code_open:{item['id']}:{mode}:{page}")
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="← Назад", callback_data=f"admin_codes_page:{mode}:{page - 1}"))
    if (page + 1) * page_size < total:
        nav_row.append(InlineKeyboardButton(text="Дальше →", callback_data=f"admin_codes_page:{mode}:{page + 1}"))
    if nav_row:
        kb.row(*nav_row)
    kb.row(InlineKeyboardButton(text="Поиск ключей", callback_data="admin_codes_search"))
    kb.row(InlineKeyboardButton(text="В меню", callback_data="admin_back"))
    return kb.as_markup()

def admin_code_detail_keyboard(code_id: int, status: str, *, mode: str, page: int) -> InlineKeyboardMarkup:
    status_normalized = (status or "").lower()
    kb = InlineKeyboardBuilder()
    if status_normalized == "active":
        kb.button(text="Деактивировать", callback_data=f"admin_code_action:revoke:{code_id}:{mode}:{page}")
    else:
        kb.button(text="Активировать", callback_data=f"admin_code_action:activate:{code_id}:{mode}:{page}")
    kb.button(text="Удалить", callback_data=f"admin_code_action:delete:{code_id}:{mode}:{page}")
    kb.button(text="Назад к списку", callback_data=f"admin_codes_back:{mode}:{page}")
    kb.button(text="В меню", callback_data="admin_back")
    kb.adjust(1)
    return kb.as_markup()
