from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.handlers.admin import _detect_node_location
from src.bot.utils.access import is_admin
from src.bot.utils.command_parse import parse_key_values

logger = logging.getLogger(__name__)

router = Router(name="server_setup")
client = BackendClient()


def _command_args(text: str) -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


@router.message(Command("setup_server", "setupserver"))
async def setup_server_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return

    raw_args = _command_args(message.text or "")
    args = parse_key_values(raw_args)
    endpoint = (args.get("ip") or args.get("endpoint") or args.get("host") or "").strip()
    password = args.get("password") or args.get("pass") or args.get("root_password") or ""
    region = (args.get("region") or args.get("region_code") or "").strip().lower()
    name = (args.get("name") or args.get("title") or "").strip()
    ssh_user = (args.get("user") or args.get("ssh_user") or "root").strip()

    # The command can contain a root password. Remove it from the chat as soon
    # as it has been read. The password is not logged or included in replies.
    if password:
        try:
            await message.delete()
        except Exception:  # noqa: BLE001
            logger.warning("could not delete VPS bootstrap command containing credentials")

    if not endpoint or not password:
        await message.answer(
            "Формат:\n"
            "/setup_server ip=1.2.3.4 password=ROOT_PASSWORD\n\n"
            "Дополнительно: region=de-frankfurt name=\"Frankfurt 1\" capacity=5.\n"
            "Регион можно не указывать — бот попробует определить его по IP.\n"
            "Политика RU/International здесь не задаётся: она применяется отдельно для каждого устройства."
        )
        return

    if ssh_user != "root":
        await message.answer("Автонастройка сейчас требует SSH-пользователя root.")
        return

    location = None
    if not region:
        location = await _detect_node_location(endpoint)
        if location:
            region = str(location.get("region_code") or "").strip().lower()[:16]
    if not region:
        await message.answer(
            "Не смог определить регион сервера. Повтори команду с region=..., например region=de-frankfurt."
        )
        return

    if not name:
        region_name = str((location or {}).get("region_name") or "").strip()
        name = region_name or region.replace("-", " ").title() or "Server"

    try:
        capacity = int(args.get("capacity") or args.get("max_users") or "5")
        bandwidth = int(args.get("bandwidth") or args.get("bandwidth_mbps") or "1000")
        speed = int(args.get("speed") or args.get("speed_mbps") or "100")
        gate_port = int(args.get("gate_port") or "24443")
    except ValueError:
        await message.answer("capacity/bandwidth/speed/gate_port должны быть числами.")
        return

    payload = {
        "name": name,
        "region_code": region,
        "endpoint": endpoint,
        "ssh_user": ssh_user,
        "ssh_password": password,
        "capacity_clients": capacity,
        "bandwidth_limit_mbps": bandwidth,
        "per_device_speed_limit_mbps": speed,
        "device_gate_host": (args.get("gate_host") or "").strip(),
        "device_gate_port": gate_port,
        "device_gate_server_name": (args.get("gate_sni") or args.get("gate_server_name") or "").strip(),
        "device_gate_spki_sha256": (args.get("gate_spki") or "").strip().lower(),
    }

    await message.answer("⚙️ Настраиваю VPS: Xray/Reality, нейтральный hostname и ключ управления.")
    try:
        result = await client.admin_bootstrap_node(payload)
    except BackendClientError as exc:
        detail = exc.detail
        if detail == "device_gate_endpoint_required":
            detail = (
                "На backend включён device-gate. Для этой ноды нужны gate_host, gate_sni и gate_spki "
                "либо автоматическая настройка gateway."
            )
        await message.answer(f"❌ Сервер не настроен: {detail}")
        return

    node = result.get("node") or {}
    await message.answer(
        "✅ VPS настроен и добавлен в пул.\n\n"
        f"ID: #{node.get('id')}\n"
        f"Название: {node.get('name')}\n"
        f"Регион: {node.get('region_code')}\n"
        f"Endpoint: {node.get('endpoint')}\n"
        f"Статус: {node.get('status')} / {node.get('health_status')}\n"
        f"Ёмкость: {node.get('current_clients')}/{node.get('capacity_clients')}\n"
        "Политики: RU и International — переключаются отдельно для каждого устройства при подключении."
    )
