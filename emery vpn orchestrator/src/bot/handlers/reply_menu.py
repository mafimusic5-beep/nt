import logging

from aiogram import F, Router
from aiogram.types import Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import admin_menu_keyboard, plans_keyboard, reply_menu_keyboard
from src.bot.utils.access import is_admin
from src.bot.utils.formatters import format_dt, plan_name
from src.common.config import settings

logger = logging.getLogger(__name__)

router = Router(name="reply_menu")
client = BackendClient()


def _reply_menu(user_id: int):
    return reply_menu_keyboard(show_admin=is_admin(user_id))


@router.message(F.text.regexp(r".*Купить / продлить$"))
async def reply_buy_handler(message: Message) -> None:
    await message.answer(
        "Выберите тариф для продукта «Прогрев».",
        reply_markup=plans_keyboard(),
    )


@router.message(F.text.regexp(r".*Мои доступы$"))
async def reply_my_access_handler(message: Message) -> None:
    telegram_id = message.from_user.id
    try:
        status = await client.get_subscription_status(telegram_id)
    except BackendClientError as exc:
        await message.answer(
            f"Не удалось получить подписку: {exc.detail}",
            reply_markup=_reply_menu(message.from_user.id),
        )
        return
    if not status.get("active"):
        await message.answer("Активной подписки нет.", reply_markup=_reply_menu(message.from_user.id))
        return
    await message.answer(
        "Моя подписка:\n"
        f"- Тариф: {plan_name(status.get('plan_code'))}\n"
        f"- Действует до: {format_dt(status.get('ends_at'))}\n"
        f"- Устройства: {status.get('devices_used', 0)}/{status.get('devices_limit', 5)}",
        reply_markup=_reply_menu(message.from_user.id),
    )


@router.message(F.text.regexp(r".*Справочник$"))
async def reply_help_handler(message: Message) -> None:
    await message.answer(
        "Помощь:\n"
        "1) Выберите «Купить подписку».\n"
        "2) Оплатите счет и нажмите «Я оплатил».\n"
        "3) Сохраните код активации — он показывается один раз полностью.",
        reply_markup=_reply_menu(message.from_user.id),
    )


@router.message(F.text.regexp(r".*Поддержка$"))
async def reply_support_handler(message: Message) -> None:
    support_url = (settings.support_url or "").strip()
    if support_url:
        await message.answer(f"Поддержка: {support_url}", reply_markup=_reply_menu(message.from_user.id))
        return
    await message.answer("Ссылка на поддержку не настроена.", reply_markup=_reply_menu(message.from_user.id))


@router.message(F.text.regexp(r".*Язык$"))
async def reply_language_handler(message: Message) -> None:
    await message.answer(
        "Смена языка пока недоступна. Сейчас используется русский язык.",
        reply_markup=_reply_menu(message.from_user.id),
    )


@router.message(F.text.regexp(r".*Админ$"))
async def reply_admin_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.", reply_markup=_reply_menu(message.from_user.id))
        return
    await message.answer("Админ-панель", reply_markup=admin_menu_keyboard())
