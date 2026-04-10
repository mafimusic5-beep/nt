from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.common.config import settings


def main_menu_keyboard(telegram_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="РљСѓРїРёС‚СЊ РїРѕРґРїРёСЃРєСѓ", callback_data="menu_buy")
    kb.button(text="РџСЂРѕРґР»РёС‚СЊ РїРѕ РєР»СЋС‡Сѓ", callback_data="menu_extend_key")
    kb.button(text="РњРѕРё РїРѕРґРїРёСЃРєРё", callback_data="menu_my_sub")
    kb.button(text="РџРѕР»СѓС‡РёС‚СЊ VPN-РєРѕРЅС„РёРі", callback_data="menu_vpn_config")
    kb.button(text="РњРѕРё СѓСЃС‚СЂРѕР№СЃС‚РІР°", callback_data="menu_my_devices")
    kb.button(text="РњРѕРё РєРѕРґС‹", callback_data="menu_my_codes")
    kb.button(text="РџРѕРјРѕС‰СЊ", callback_data="menu_help")
    if telegram_id in settings.admin_id_list:
        kb.button(text="РђРґРјРёРЅ", callback_data="menu_admin")
    kb.button(text="РџРѕРґРґРµСЂР¶РєР°", url=settings.support_url)
    kb.button(text="РљР°РЅР°Р»", url=settings.channel_url)
    kb.adjust(1)
    return kb.as_markup()


def plans_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="1 РјРµСЃСЏС† вЂ” 600 СЂСѓР±", callback_data="buy_warmup_1m")
    kb.button(text="3 РјРµСЃСЏС†Р° вЂ” 1500 СЂСѓР±", callback_data="buy_warmup_3m")
    kb.button(text="6 РјРµСЃСЏС†РµРІ вЂ” 2700 СЂСѓР±", callback_data="buy_warmup_6m")
    kb.button(text="12 РјРµСЃСЏС†РµРІ вЂ” 4800 СЂСѓР±", callback_data="buy_warmup_12m")
    kb.button(text="РќР°Р·Р°Рґ РІ РјРµРЅСЋ", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()


def renew_mode_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="РџСЂРѕРґР»РёС‚СЊ СЌС‚РѕС‚ Р¶Рµ РєР»СЋС‡", callback_data="renewmode_keep")
    kb.button(text="РџСЂРѕРґР»РёС‚СЊ Рё РІС‹РґР°С‚СЊ РЅРѕРІС‹Р№ РєР»СЋС‡", callback_data="renewmode_new")
    kb.button(text="РќР°Р·Р°Рґ РІ РјРµРЅСЋ", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()


def renew_plans_keyboard(mode: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="1 РјРµСЃСЏС† вЂ” 600 СЂСѓР±", callback_data=f"renewplan_{mode}_warmup_1m")
    kb.button(text="3 РјРµСЃСЏС†Р° вЂ” 1500 СЂСѓР±", callback_data=f"renewplan_{mode}_warmup_3m")
    kb.button(text="6 РјРµСЃСЏС†РµРІ вЂ” 2700 СЂСѓР±", callback_data=f"renewplan_{mode}_warmup_6m")
    kb.button(text="12 РјРµСЃСЏС†РµРІ вЂ” 4800 СЂСѓР±", callback_data=f"renewplan_{mode}_warmup_12m")
    kb.button(text="РќР°Р·Р°Рґ РІ РјРµРЅСЋ", callback_data="menu_back")
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
            [InlineKeyboardButton(text="РЇ РѕРїР»Р°С‚РёР»", callback_data=callback_data)],
            [InlineKeyboardButton(text="РќР°Р·Р°Рґ РІ РјРµРЅСЋ", callback_data="menu_back")],
        ]
    )


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Р’СЃРµ РєР»СЋС‡Рё", callback_data="admin_keys_0")
    kb.button(text="РЎС‚Р°С‚РёСЃС‚РёРєР°", callback_data="admin_stats")
    kb.button(text="РЎРїРёСЃРѕРє СѓР·Р»РѕРІ", callback_data="admin_nodes")
    kb.button(text="Р’С‹РґР°С‚СЊ СЃРµР±Рµ +1 РјРµСЃСЏС†", callback_data="admin_grant_self")
    kb.button(text="РЎРіРµРЅРµСЂРёСЂРѕРІР°С‚СЊ РєРѕРґ СЃРµР±Рµ", callback_data="admin_code_self")
    kb.button(text="РџСЂРѕР±Р»РµРјРЅС‹Рµ Р°РєС‚РёРІР°С†РёРё", callback_data="admin_problem_activations")
    kb.button(text="РќР°Р·Р°Рґ РІ РјРµРЅСЋ", callback_data="menu_back")
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
        nav_row.append(InlineKeyboardButton(text="в—ЂпёЏ", callback_data=f"admin_keys_{page - 1}"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="в–¶пёЏ", callback_data=f"admin_keys_{page + 1}"))
    if nav_row:
        kb.row(*nav_row)
    kb.row(InlineKeyboardButton(text="РќР°Р·Р°Рґ", callback_data="menu_admin"))
    kb.adjust(1)
    return kb.as_markup()


def admin_key_card_keyboard(code_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="РЈРґР°Р»РёС‚СЊ", callback_data=f"admin_key_delete_{code_id}_{page}")],
            [InlineKeyboardButton(text="РЎРіРµРЅРµСЂРёСЂРѕРІР°С‚СЊ РЅРѕРІС‹Р№ РєРѕРґ", callback_data=f"admin_key_generate_{code_id}_{page}")],
            [InlineKeyboardButton(text="РќР°Р·Р°Рґ Рє СЃРїРёСЃРєСѓ", callback_data=f"admin_keys_{page}")],
        ]
    )
