import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import admin_menu_keyboard, main_menu_keyboard, user_reply_keyboard
from src.bot.utils.access import is_admin
from src.bot.utils.formatters import format_dt

logger = logging.getLogger(__name__)
router = Router(name="admin_menu")
client = BackendClient()
ADMIN_TRIGGER_TEXTS = {"👑 Админ", "Админ"}
ADMIN_CALLBACKS = {
    "admin_stats",
    "admin_nodes",
    "admin_grant_self",
    "admin_code_self",
    "admin_problem_activations",
    "admin_back",
}


async def _show_admin_panel(message: Message) -> None:
    await message.answer("Панель закреплена снизу.", reply_markup=user_reply_keyboard(include_admin=True))
    await message.answer("Админ-панель", reply_markup=admin_menu_keyboard())


@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    await _show_admin_panel(message)


@router.message(F.text.in_(ADMIN_TRIGGER_TEXTS))
async def admin_button_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    await _show_admin_panel(message)


@router.callback_query(F.data.in_(ADMIN_CALLBACKS))
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
                    lines.append(f"- {format_dt(row.get('created_at'))} | {row.get('action')} | actor={row.get('actor_id')}")
                text = "\n".join(lines)
            await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
        else:
            await callback.message.edit_text("Админ-панель", reply_markup=admin_menu_keyboard())
        await callback.answer()
    except BackendClientError as exc:
        logger.warning("admin backend call failed: action=%s err=%s", data, exc.detail)
        await callback.message.edit_text(f"Ошибка backend: {exc.detail}", reply_markup=admin_menu_keyboard())
        await callback.answer()
