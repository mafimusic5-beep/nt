from __future__ import annotations

import builtins
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _ensure_project_path() -> None:
    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / "emery vpn orchestrator",
        base_dir,
    ]
    for candidate in candidates:
        if (candidate / "src").exists():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
            return


def _replace_message_callback(router, callback_name: str, new_callback) -> None:
    observer = getattr(router, "message", None)
    handlers = getattr(observer, "handlers", None)
    if not handlers:
        return
    for handler in handlers:
        callback = getattr(handler, "callback", None)
        if getattr(callback, "__name__", "") == callback_name:
            handler.callback = new_callback
            return


def _patch_reply_menu() -> None:
    if getattr(builtins, "_emery_reply_menu_patch_applied", False):
        return

    builtins._emery_reply_menu_patch_applied = True
    _ensure_project_path()

    from aiogram import F
    from aiogram.types import (
        InlineKeyboardMarkup,
        KeyboardButton,
        Message,
        ReplyKeyboardMarkup,
    )
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    from src.bot.handlers import admin as admin_module
    from src.bot.handlers import start as start_module
    from src.bot.handlers import subscription as subscription_module
    from src.bot.ui.keyboards import admin_menu_keyboard
    from src.bot.utils.access import is_admin
    from src.common.config import settings

    if getattr(start_module, "__reply_menu_patch_applied__", False):
        return

    def reply_main_menu_keyboard(telegram_id: int | None = None) -> ReplyKeyboardMarkup:
        rows = [
            [
                KeyboardButton(text="🚀 Купить / продлить"),
                KeyboardButton(text="📦 Мои доступы"),
            ],
            [
                KeyboardButton(text="🧠 Справочник"),
                KeyboardButton(text="🛟 Поддержка"),
            ],
            [KeyboardButton(text="🌐 Язык")],
        ]
        if telegram_id is not None and is_admin(telegram_id):
            rows.append([KeyboardButton(text="👑 Админ")])
        return ReplyKeyboardMarkup(
            keyboard=rows,
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="Выберите действие",
        )

    def access_menu_keyboard() -> InlineKeyboardMarkup:
        kb = InlineKeyboardBuilder()
        kb.button(text="Моя подписка", callback_data="menu_my_sub")
        kb.button(text="Получить VPN-конфиг", callback_data="menu_vpn_config")
        kb.button(text="Мои устройства", callback_data="menu_my_devices")
        kb.button(text="Мои коды", callback_data="menu_my_codes")
        kb.button(text="Назад в меню", callback_data="menu_back")
        kb.adjust(1)
        return kb.as_markup()

    def support_keyboard() -> InlineKeyboardMarkup | None:
        support_url = (settings.support_url or "").strip()
        if not support_url:
            return None
        kb = InlineKeyboardBuilder()
        kb.button(text="Открыть поддержку", url=support_url)
        kb.adjust(1)
        return kb.as_markup()

    async def patched_start_handler(message: Message) -> None:
        await message.answer(
            f"Добро пожаловать в {settings.brand_name}.\n"
            "Выберите действие в меню ниже.",
            reply_markup=reply_main_menu_keyboard(message.from_user.id),
        )
        await message.answer(
            "Быстрые действия:",
            reply_markup=start_module.main_menu_keyboard(),
        )

    async def patched_menu_handler(message: Message) -> None:
        await message.answer(
            "Главное меню",
            reply_markup=reply_main_menu_keyboard(message.from_user.id),
        )
        await message.answer(
            "Быстрые действия:",
            reply_markup=start_module.main_menu_keyboard(),
        )

    _replace_message_callback(start_module.router, "start_handler", patched_start_handler)
    _replace_message_callback(subscription_module.router, "menu_handler", patched_menu_handler)

    buy_texts = {"🚀 Купить / продлить", "Купить / продлить", "Купить подписку"}
    access_texts = {"📦 Мои доступы", "Мои доступы"}
    help_texts = {"🧠 Справочник", "Справочник", "Помощь"}
    support_texts = {"🛟 Поддержка", "Поддержка"}
    language_texts = {"🌐 Язык", "Язык"}
    admin_texts = {"👑 Админ", "Админ"}

    @start_module.router.message(F.text.in_(buy_texts))
    async def menu_buy_text_handler(message: Message) -> None:
        await message.answer(
            "Выберите тариф для продукта «Прогрев».",
            reply_markup=start_module.plans_keyboard(),
        )

    @start_module.router.message(F.text.in_(access_texts))
    async def menu_access_text_handler(message: Message) -> None:
        await message.answer(
            "Мои доступы:\n"
            "— проверьте подписку\n"
            "— получите VPN-конфиг\n"
            "— посмотрите устройства и коды",
            reply_markup=access_menu_keyboard(),
        )

    @start_module.router.message(F.text.in_(help_texts))
    async def menu_help_text_handler(message: Message) -> None:
        await message.answer(
            "Справочник:\n"
            "1) Нажмите «Купить / продлить».\n"
            "2) Выберите тариф и оплатите счет.\n"
            "3) После оплаты нажмите «Я оплатил».\n"
            "4) Сохраните код активации — он показывается один раз полностью.",
        )

    @start_module.router.message(F.text.in_(support_texts))
    async def menu_support_text_handler(message: Message) -> None:
        support_url = (settings.support_url or "").strip()
        if not support_url or support_url.endswith("/your_support"):
            await message.answer("Ссылка на поддержку еще не настроена.")
            return
        await message.answer(
            f"Поддержка: {support_url}",
            disable_web_page_preview=True,
            reply_markup=support_keyboard(),
        )

    @start_module.router.message(F.text.in_(language_texts))
    async def menu_language_text_handler(message: Message) -> None:
        await message.answer("Сейчас доступен только русский язык.")

    @admin_module.router.message(F.text.in_(admin_texts))
    async def admin_text_handler(message: Message) -> None:
        if not is_admin(message.from_user.id):
            await message.answer("Доступ запрещен.")
            return
        await message.answer("Админ-панель", reply_markup=admin_menu_keyboard())

    start_module.__reply_menu_patch_applied__ = True
    admin_module.__reply_menu_patch_applied__ = True


try:
    _patch_reply_menu()
except Exception as exc:
    logger.debug("reply-menu hotfix was not applied: %s", exc)
