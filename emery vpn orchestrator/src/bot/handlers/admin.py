import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import admin_menu_keyboard, main_menu_keyboard
from src.bot.utils.access import is_admin
from src.bot.utils.formatters import format_dt

logger = logging.getLogger(__name__)

router = Router(name="admin_menu")
client = BackendClient()


def _user_menu_markup(user_id: int):
    return main_menu_keyboard(show_admin=is_admin(user_id))


def _admin_codes_keyboard(rows: list[dict]):
    kb = InlineKeyboardBuilder()
    for row in rows:
        kb.button(
            text=f"#{row.get('code_id')} | sub {row.get('subscription_id')} | {row.get('code_hash_prefix')}",
            callback_data=f"admin_code_{row.get('code_id')}",
        )
    kb.button(text="Назад в админку", callback_data="menu_admin")
    kb.adjust(1)
    return kb.as_markup()


@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    await message.answer("Админ-панель", reply_markup=admin_menu_keyboard())


@router.callback_query(F.data.startswith("admin_"))
async def admin_callbacks_handler(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    data = callback.data
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
        elif data == "admin_codes":
            rows = await client.admin_codes(limit=20, offset=0)
            if not rows:
                text = "Записей ключей пока нет."
                markup = admin_menu_keyboard()
            else:
                lines = ["Все ключи (записи):"]
                for row in rows:
                    lines.append(
                        f"- #{row.get('code_id')} | tg={row.get('telegram_id') or '-'} | "
                        f"sub={row.get('subscription_id')} | {row.get('status')} | "
                        f"hash={row.get('code_hash_prefix')} | до {format_dt(row.get('ends_at'))}"
                    )
                text = "\n".join(lines)
                markup = _admin_codes_keyboard(rows)
            await callback.message.edit_text(text, reply_markup=markup)
        elif data.startswith("admin_code_"):
            try:
                code_id = int(data.rsplit("_", 1)[1])
            except ValueError:
                await callback.answer("Некорректный ID ключа.", show_alert=True)
                return
            row = await client.admin_code_detail(code_id)
            await callback.message.edit_text(
                "Карточка ключа:\n"
                f"- Code ID: {row.get('code_id')}\n"
                f"- Hash prefix: {row.get('code_hash_prefix')}\n"
                f"- Статус: {row.get('status')}\n"
                f"- Telegram ID: {row.get('telegram_id')}\n"
                f"- User ID: {row.get('user_id')}\n"
                f"- Subscription ID: {row.get('subscription_id')}\n"
                f"- Тариф: {row.get('plan_code') or '-'}\n"
                f"- Действует до: {format_dt(row.get('ends_at'))}\n"
                f"- Устройства: {row.get('devices_used', 0)}/{row.get('devices_limit', 0)}\n"
                f"- Создан: {format_dt(row.get('created_at'))}\n"
                f"- Первое использование: {format_dt(row.get('first_redeemed_at'))}",
                reply_markup=_admin_codes_keyboard([row]),
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
        else:
            await callback.message.edit_text("Неизвестная админ-команда.", reply_markup=_user_menu_markup(callback.from_user.id))
        await callback.answer()
    except BackendClientError as exc:
        logger.warning("admin backend call failed: action=%s err=%s", data, exc.detail)
        await callback.message.edit_text(
            f"Ошибка backend: {exc.detail}",
            reply_markup=admin_menu_keyboard(),
        )
        await callback.answer()
