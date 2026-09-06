import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.bot.handlers import admin_node_delete


def _message(text: str):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=123),
        answer=AsyncMock(),
    )


def test_delconfig_calls_delete_endpoint(monkeypatch):
    message = _message("/delconfig #42")
    request = AsyncMock(return_value={"node_id": 42, "detail": "deleted", "removed_assignments": 0})
    monkeypatch.setattr(admin_node_delete, "is_admin", lambda _user_id: True)
    monkeypatch.setattr(
        admin_node_delete,
        "client",
        SimpleNamespace(admin_api_key="key", _request=request),
    )

    asyncio.run(admin_node_delete.delete_config(message))

    request.assert_awaited_once_with(
        "DELETE",
        "/api/v1/admin/nodes/42",
        headers={"X-Admin-Api-Key": "key"},
        timeout_seconds=300.0,
    )
    assert "полностью удалён" in message.answer.await_args.args[0]


def test_delconfig_rejects_invalid_id(monkeypatch):
    message = _message("/delconfig nope")
    request = AsyncMock()
    monkeypatch.setattr(admin_node_delete, "is_admin", lambda _user_id: True)
    monkeypatch.setattr(
        admin_node_delete,
        "client",
        SimpleNamespace(admin_api_key="key", _request=request),
    )

    asyncio.run(admin_node_delete.delete_config(message))

    request.assert_not_awaited()
    assert message.answer.await_args.args[0].startswith("Формат: /delconfig ID")
