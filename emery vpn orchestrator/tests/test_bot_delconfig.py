import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.bot.handlers import admin


def _message(text: str):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=123),
        answer=AsyncMock(),
    )


def test_delconfig_disables_pool_node(monkeypatch):
    message = _message("/delconfig@SkryonBot #42")
    disable_node = AsyncMock(return_value={"node_id": 42, "detail": "disabled"})
    monkeypatch.setattr(admin, "is_admin", lambda _user_id: True)
    monkeypatch.setattr(admin, "client", SimpleNamespace(admin_disable_node=disable_node))

    asyncio.run(admin.delconfig_command_handler(message))

    disable_node.assert_awaited_once_with(42)
    response = message.answer.await_args.args[0]
    assert "#42" in response
    assert "синхронизированных приложений" in response


def test_delconfig_rejects_invalid_id(monkeypatch):
    message = _message("/delconfig nope")
    disable_node = AsyncMock()
    monkeypatch.setattr(admin, "is_admin", lambda _user_id: True)
    monkeypatch.setattr(admin, "client", SimpleNamespace(admin_disable_node=disable_node))

    asyncio.run(admin.delconfig_command_handler(message))

    disable_node.assert_not_awaited()
    assert message.answer.await_args.args[0].startswith("Формат: /delconfig ID")
