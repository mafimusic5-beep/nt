import asyncio

from aiogram.types import User

from src.bot.middlewares import admin_only


def test_non_admin_update_is_silently_discarded(monkeypatch):
    monkeypatch.setattr(admin_only, "is_admin", lambda telegram_id: telegram_id == 111)
    middleware = admin_only.AdminOnlyMiddleware()
    called = False

    async def handler(event, data):
        nonlocal called
        called = True
        return "handled"

    result = asyncio.run(
        middleware(
            handler,
            object(),
            {"event_from_user": User(id=222, is_bot=False, first_name="Other")},
        )
    )

    assert result is None
    assert called is False


def test_admin_update_reaches_handler(monkeypatch):
    monkeypatch.setattr(admin_only, "is_admin", lambda telegram_id: telegram_id == 111)
    middleware = admin_only.AdminOnlyMiddleware()

    async def handler(event, data):
        return "handled"

    result = asyncio.run(
        middleware(
            handler,
            object(),
            {"event_from_user": User(id=111, is_bot=False, first_name="Admin")},
        )
    )

    assert result == "handled"


def test_update_without_user_is_discarded(monkeypatch):
    monkeypatch.setattr(admin_only, "is_admin", lambda telegram_id: True)
    middleware = admin_only.AdminOnlyMiddleware()
    called = False

    async def handler(event, data):
        nonlocal called
        called = True
        return "handled"

    result = asyncio.run(middleware(handler, object(), {}))

    assert result is None
    assert called is False
