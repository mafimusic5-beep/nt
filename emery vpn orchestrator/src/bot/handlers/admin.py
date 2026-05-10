import logging
from urllib.parse import unquote

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import admin_menu_keyboard, admin_reply_keyboard, main_menu_keyboard
from src.bot.utils.access import is_admin
from src.bot.utils.command_parse import endpoint_from_proxy_link, extract_first_proxy_link, parse_key_values
from src.bot.utils.formatters import format_dt
from src.common.config import settings

logger = logging.getLogger(__name__)

router = Router(name="admin_menu")
client = BackendClient()
ADMIN_TRIGGER_TEXTS = {"👑 Админ", "Админ"}


async def _show_admin_panel(message: Message) -> None:
    await message.answer("Панель закреплена снизу.", reply_markup=admin_reply_keyboard())
    await message.answer(
        "Админ-панель\n\n"
        "Быстрые команды:\n"
        "/capacity — какого региона не хватает\n"
        "/add_config <VLESS Reality ссылка> — добавить сервер в общий пул\n"
        "/add_config region=nl name=\"Netherlands 1\" capacity=50 config=<VLESS Reality ссылка>\n"
        "/servers — список узлов",
        reply_markup=admin_menu_keyboard(),
    )


@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    await _show_admin_panel(message)


@router.message(Command("servers"))
async def servers_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    try:
        nodes = await client.admin_nodes()
    except BackendClientError as exc:
        await message.answer(f"Ошибка backend: {exc.detail}")
        return
    await message.answer(_format_nodes(nodes))


@router.message(Command("capacity"))
async def capacity_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    try:
        payload = await client.admin_capacity_alert()
    except BackendClientError as exc:
        await message.answer(f"Ошибка backend: {exc.detail}")
        return
    await message.answer(payload.get("text") or "Нет данных по ёмкости.")


@router.message(Command("add_config"))
async def add_config_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return

    raw_args = _command_args(message.text or "", "/add_config")
    args = parse_key_values(raw_args)
    link = args.get("config") or args.get("link") or extract_first_proxy_link(raw_args)
    region = (args.get("region") or args.get("region_code") or settings.default_region_code).strip().lower()
    name = (args.get("name") or args.get("title") or _name_from_proxy_link(link)).strip()
    endpoint = (args.get("endpoint") or args.get("ip") or endpoint_from_proxy_link(link)).strip()
    provider = (args.get("provider") or "manual").strip().lower()
    max_users_raw = args.get("max_users") or args.get("capacity") or "50"

    if not link:
        await message.answer(
            "Формат:\n"
            "/add_config <VLESS Reality ссылка>\n\n"
            "Можно без region/name: регион возьмется из настроек, имя — из части после #."
        )
        return
    if not link.lower().startswith("vless://"):
        await message.answer("Конфиг должен начинаться с vless://")
        return
    if not endpoint:
        await message.answer(
            "Не смог определить endpoint из ссылки. "
            "Укажи endpoint=1.2.3.4 или проверь формат ссылки."
        )
        return
    try:
        max_users = int(max_users_raw)
    except ValueError:
        max_users = 50
    if not name:
        name = f"{region.upper()} manual node"

    payload = {
        "name": name,
        "region_code": region,
        "provider": provider,
        "endpoint": endpoint,
        "config_payload": link,
        "status": "active",
        "health_status": "healthy",
        "load_score": 100,
        "priority": 0,
        "capacity_clients": max_users,
        "bandwidth_limit_mbps": 1000,
        "current_clients": 0,
        "per_device_speed_limit_mbps": 100,
    }
    try:
        node = await client.admin_create_node(payload)
    except BackendClientError as exc:
        await message.answer(f"Не добавил конфиг. Ошибка backend: {exc.detail}")
        return

    await message.answer(
        "✅ Конфиг добавлен в общий пул.\n\n"
        f"ID: #{node.get('id')}\n"
        f"Название: {node.get('name')}\n"
        f"Регион: {node.get('region_code')}\n"
        f"Provider: {node.get('provider')}\n"
        f"Endpoint: {node.get('endpoint')}\n"
        f"Статус: {node.get('status')} / {node.get('health_status')}\n"
        f"Ёмкость: {node.get('current_clients')}/{node.get('capacity_clients')}"
    )


@router.message(F.text.in_(ADMIN_TRIGGER_TEXTS))
async def admin_button_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    await _show_admin_panel(message)


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
        elif data == "admin_nodes":
            nodes = await client.admin_nodes()
            await callback.message.edit_text(_format_nodes(nodes), reply_markup=admin_menu_keyboard())
        elif data == "admin_capacity":
            payload = await client.admin_capacity()
            rows = payload.get("regions", []) if isinstance(payload, dict) else []
            await callback.message.edit_text(_format_capacity_rows(rows), reply_markup=admin_menu_keyboard())
        elif data == "admin_capacity_alert":
            payload = await client.admin_capacity_alert()
            await callback.message.edit_text(payload.get("text") or "Нет данных по ёмкости.", reply_markup=admin_menu_keyboard())
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
        elif data == "admin_back":
            await callback.message.edit_text("Админ-панель", reply_markup=admin_menu_keyboard())
        else:
            await callback.message.edit_text("Неизвестная админ-команда.", reply_markup=main_menu_keyboard())
        await callback.answer()
    except BackendClientError as exc:
        logger.warning("admin backend call failed: action=%s err=%s", data, exc.detail)
        await callback.message.edit_text(
            f"Ошибка backend: {exc.detail}",
            reply_markup=admin_menu_keyboard(),
        )
        await callback.answer()


def _command_args(text: str, command: str) -> str:
    if text.startswith(command):
        return text[len(command):].strip()
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _name_from_proxy_link(link: str) -> str:
    try:
        return unquote(link.split("#", 1)[1]).strip()
    except Exception:
        return ""


def _format_nodes(nodes: list[dict]) -> str:
    if not nodes:
        return "Узлы не найдены. Добавь первый через /add_config."
    lines = ["Список узлов:"]
    for n in nodes[:30]:
        lines.append(
            f"- #{n.get('id')} {n.get('name')} [{n.get('region_code')}] "
            f"{n.get('provider', 'manual')} {n.get('status')}/{n.get('health_status')} "
            f"{n.get('current_clients')}/{n.get('capacity_clients')} {n.get('endpoint')}"
        )
    return "\n".join(lines)


def _format_capacity_rows(rows: list[dict]) -> str:
    if not rows:
        return "Серверов пока нет. Купи первый VPS и добавь /add_config."
    lines = ["📊 Ёмкость регионов:", ""]
    for row in rows:
        lines.append(
            f"{row.get('region_code')}: {row.get('fill_percent')}% "
            f"({row.get('current_clients')}/{row.get('total_capacity')}, "
            f"свободно {row.get('free_slots')}) — {row.get('status')}"
        )
    return "\n".join(lines)
