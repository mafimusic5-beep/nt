from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.utils.access import is_admin

router = Router(name="admin_slots")
client = BackendClient()
_cleanup_nodes: set[int] = set()


def _args(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _is_admin(message: Message) -> bool:
    return bool(message.from_user) and is_admin(message.from_user.id)


@router.message(Command("configs", "nodes"))
async def configs_with_explicit_slot_semantics(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Доступ запрещен.")
        return
    try:
        nodes = await client.admin_nodes()
    except BackendClientError as exc:
        await message.answer(f"Ошибка backend: {exc.detail}")
        return
    if not nodes:
        await message.answer("Узлы не найдены.")
        return

    lines = ["Список узлов:"]
    for node in nodes[:30]:
        lines.append(
            f"- #{node.get('id')} {node.get('name')} [{node.get('region_code')}] "
            f"{node.get('status')}/{node.get('health_status')} | "
            f"слоты {node.get('current_clients')}/{node.get('capacity_clients')}"
        )
    lines.append("")
    lines.append("Слоты — это выданные device-assignment записи, а не число пользователей онлайн.")
    await message.answer("\n".join(lines))


@router.message(Command("clear_slots", "clearslots"))
async def clear_stale_slots(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Доступ запрещен.")
        return

    raw = _args(message).removeprefix("#").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("Формат: /clear_slots NODE_ID")
        return
    node_id = int(raw)

    if node_id in _cleanup_nodes:
        await message.answer(
            f"⏳ Очистка слотов VPS #{node_id} уже выполняется. "
            "Повторно запускать команду не нужно — дождись итогового сообщения."
        )
        return

    _cleanup_nodes.add(node_id)
    try:
        await message.answer(
            f"🧹 Очищаю stale assignment-слоты на VPS #{node_id}. "
            "На время очистки узел будет исключён из пула.\n"
            "Это может занять несколько минут; повторно /clear_slots не запускай."
        )
        try:
            result = await client._request(
                "POST",
                f"/api/v1/admin/nodes/{node_id}/assignments/clear",
                headers={"X-Admin-Api-Key": client.admin_api_key},
                timeout_seconds=900.0,
            )
        except BackendClientError as exc:
            if exc.status_code == 409 and "cleanup_in_progress" in exc.detail:
                await message.answer(
                    f"⏳ Очистка слотов VPS #{node_id} уже идёт на backend. "
                    "Дождись её завершения и затем проверь /configs."
                )
            else:
                await message.answer(f"❌ Не очистил слоты. Backend: {exc.detail}")
            return

        cleared = int(result.get("cleared") or 0)
        failed = int(result.get("failed") or 0)
        remaining = int(result.get("remaining") or 0)
        status = str(result.get("node_status") or "unknown")
        health = str(result.get("health_status") or "unknown")
        if failed:
            await message.answer(
                "⚠️ Очистка завершилась не полностью.\n"
                f"Удалено: {cleared}\n"
                f"Не удалено: {failed}\n"
                f"Осталось слотов: {remaining}\n"
                f"Статус узла: {status}/{health}\n"
                "Узел оставлен вне пула fail-closed."
            )
            return

        await message.answer(
            "✅ Stale assignment-слоты очищены.\n"
            f"Удалено: {cleared}\n"
            f"Осталось слотов: {remaining}\n"
            f"Статус узла: {status}/{health}\n"
            "Проверь /configs."
        )
    finally:
        _cleanup_nodes.discard(node_id)
