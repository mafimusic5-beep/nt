import asyncio
import ipaddress
import logging
import re
import socket
import unicodedata
from urllib.parse import unquote

import httpx
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import admin_menu_keyboard, admin_reply_keyboard, main_menu_keyboard
from src.bot.utils.access import is_admin
from src.bot.utils.command_parse import endpoint_from_proxy_link, extract_first_proxy_link, parse_key_values
from src.bot.utils.formatters import format_dt

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
        "/add_config <VLESS Reality ссылка> — определить локацию и добавить сервер в общий пул\n"
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
    endpoint = (args.get("endpoint") or args.get("ip") or endpoint_from_proxy_link(link)).strip()
    explicit_region = (args.get("region") or args.get("region_code") or "").strip().lower()
    explicit_name = (args.get("name") or args.get("title") or "").strip()
    provider = (args.get("provider") or "manual").strip().lower()
    max_users_raw = args.get("max_users") or args.get("capacity") or "50"

    if not link:
        await message.answer(
            "Формат:\n"
            "/add_config <VLESS Reality ссылка>\n\n"
            "Бот сам определит страну/город по IP из ссылки. "
            "Если GeoIP недоступен, можно указать region=... вручную."
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

    location = await _detect_node_location(endpoint) if not explicit_region else None
    if not explicit_region and not location:
        await message.answer(
            "Не смог определить локацию сервера по endpoint.\n\n"
            "Конфиг не добавлен, чтобы не создавать регион-заглушку. "
            "Попробуй позже или укажи region=country-city вручную."
        )
        return

    region = explicit_region or location["region_code"]
    name = _build_node_name(explicit_name, _name_from_proxy_link(link), location, region)

    try:
        max_users = int(max_users_raw)
    except ValueError:
        max_users = 50

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

    location_line = ""
    if location:
        location_line = f"Локация: {location['region_name']} ({location['country_code']})\n"
    await message.answer(
        "✅ Конфиг добавлен в общий пул.\n\n"
        f"ID: #{node.get('id')}\n"
        f"Название: {node.get('name')}\n"
        f"Регион: {node.get('region_code')}\n"
        f"{location_line}"
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


def _build_node_name(explicit_name: str, link_name: str, location: dict | None, region: str) -> str:
    if explicit_name:
        return explicit_name
    region_name = str((location or {}).get("region_name") or "").strip()
    if link_name and region_name and region_name.lower() not in link_name.lower():
        return f"{link_name} ({region_name})"
    if link_name:
        return link_name
    if region_name:
        return f"{region_name} node"
    return f"{region.upper()} manual node"


async def _detect_node_location(endpoint: str) -> dict | None:
    ip = await _resolve_public_ip(endpoint)
    if not ip:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            response = await http.get(f"https://ipapi.co/{ip}/json/")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("geoip lookup failed: endpoint=%s ip=%s err=%s", endpoint, ip, exc)
        return None

    if not isinstance(payload, dict) or payload.get("error"):
        return None
    country_code = str(payload.get("country_code") or payload.get("country") or "").strip().lower()
    city = str(payload.get("city") or "").strip()
    country_name = str(payload.get("country_name") or "").strip()
    if not country_code:
        return None

    city_slug = _slugify(city)
    if city_slug:
        region_code = f"{country_code}-{city_slug}"
        region_name = city
    else:
        region_code = country_code
        region_name = country_name or country_code.upper()
    return {
        "ip": ip,
        "country_code": country_code.upper(),
        "region_code": region_code[:64],
        "region_name": region_name,
    }


async def _resolve_public_ip(endpoint: str) -> str | None:
    host = endpoint.strip().strip("[]")
    if not host:
        return None
    try:
        parsed = ipaddress.ip_address(host)
        return str(parsed) if _is_public_ip(parsed) else None
    except ValueError:
        pass
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
    except OSError:
        return None
    for family, _, _, _, sockaddr in infos:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        try:
            parsed = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _is_public_ip(parsed):
            return str(parsed)
    return None


def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or ""


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
