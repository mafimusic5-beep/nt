from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.utils.access import is_admin


router = Router(name="admin_node_delete")
client = BackendClient()


def _args(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


@router.message(Command("delconfig", "del_config"))
async def delete_config(message: Message) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return

    raw = _args(message).removeprefix("#").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("Формат: /delconfig ID\nID сервера можно посмотреть через /configs")
        return
    node_id = int(raw)

    try:
        result = await client._request(
            "DELETE",
            f"/api/v1/admin/nodes/{node_id}",
            headers={"X-Admin-Api-Key": client.admin_api_key},
            timeout_seconds=300.0,
        )
    except BackendClientError as exc:
        if exc.status_code == 404:
            await message.answer("Сервер уже удалён или не найден.")
        elif exc.status_code == 409:
            await message.answer(
                "❌ Сервер не удалён: не удалось безопасно снять все его device-assignment credentials. "
                "Узел оставлен вне пула."
            )
        else:
            await message.answer(f"❌ Не удалил сервер. Backend: {exc.detail}")
        return

    removed = int(result.get("removed_assignments") or 0)
    await message.answer(
        f"✅ Сервер #{node_id} полностью удалён из пула и базы.\n"
        f"Удалено assignment-записей: {removed}.\n"
        "Проверь /configs — узел больше не должен отображаться."
    )
