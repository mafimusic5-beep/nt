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


@router.message(F.text.in_({"👑 Админ", "Админ"}))
async def admin_text_button_handler(message: Message) -> None:
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
