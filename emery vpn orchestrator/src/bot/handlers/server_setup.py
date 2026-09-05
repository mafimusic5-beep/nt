from __future__ import annotations

import logging
import shlex

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


def _credentials(raw_args: str) -> tuple[str, str]:
    """Accept the short form `/setup_server IP PASSWORD`.

    Keep the old key=value form working for existing admin notes/scripts, but it
    is no longer required or advertised to the admin.
    """
    args = parse_key_values(raw_args)
    endpoint = (args.get("ip") or args.get("endpoint") or args.get("host") or "").strip()
    password = args.get("password") or args.get("pass") or args.get("root_password") or ""
    if endpoint and password:
        return endpoint, password

    try:
        parts = shlex.split(raw_args)
    except ValueError:
        return "", ""
    if len(parts) != 2 or "=" in parts[0]:
        return "", ""
    return parts[0].strip(), parts[1]


@router.message(Command("setup_server", "setupserver"))
async def setup_server_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return

    raw_args = _command_args(message.text or "")
    endpoint, password = _credentials(raw_args)

    # The command contains a root password. Remove it from the chat as soon as
    # it has been read. The password is not logged or included in replies.
    if password:
        try:
            await message.delete()
        except Exception:  # noqa: BLE001
            logger.warning("could not delete VPS bootstrap command containing credentials")

    if not endpoint or not password:
        await message.answer("Формат: /setup_server 1.2.3.4 ROOT_PASSWORD")
        return

    # Everything except endpoint/password is automatic. GeoIP improves the
    # node label, but a temporary GeoIP failure must not block provisioning.
    location = await _detect_node_location(endpoint)
    region = str((location or {}).get("region_code") or "auto").strip().lower()[:16] or "auto"
    region_name = str((location or {}).get("region_name") or "").strip()
    name = region_name or "Server"

    payload = {
        "name": name,
        "region_code": region,
        "endpoint": endpoint,
        "ssh_user": "root",
        "ssh_password": password,
        "capacity_clients": 5,
        "bandwidth_limit_mbps": 1000,
        "per_device_speed_limit_mbps": 100,
        "device_gate_host": "",
        "device_gate_port": 24443,
        "device_gate_server_name": "",
        "device_gate_spki_sha256": "",
    }

    await message.answer("⚙️ Настраиваю VPS и добавляю его в пул.")
    try:
        result = await client.admin_bootstrap_node(payload)
    except BackendClientError as exc:
        method = exc.method or "POST"
        path = exc.path or "/api/v1/admin/nodes/bootstrap"
        base_url = exc.base_url or client.base_url
        logger.error(
            "setup_server failed status=%s method=%s path=%s base_url=%s detail=%s",
            exc.status_code,
            method,
            path,
            base_url,
            str(exc.detail)[:160],
        )
        await message.answer(
            f"❌ Сервер не настроен: {exc.detail}\n\n"
            f"🔎 HTTP {exc.status_code} · {method} {path}\n"
            f"Backend: {base_url}"
        )
        return

    node = result.get("node") or {}
    egress_label = "ISP / WireGuard" if result.get("isp_egress_enabled") else "VPS direct"
    await message.answer(
        "✅ VPS настроен и добавлен в пул.\n\n"
        f"ID: #{node.get('id')}\n"
        f"Название: {node.get('name')}\n"
        f"Регион: {node.get('region_code')}\n"
        f"Endpoint: {node.get('endpoint')}\n"
        f"Выход: {egress_label}\n"
        f"Статус: {node.get('status')} / {node.get('health_status')}\n"
        f"Слоты пула: {node.get('current_clients')}/{node.get('capacity_clients')}\n"
        "(это выданные device assignments, не число пользователей онлайн)"
    )
