from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from src.bot.utils.access import is_admin


class AdminOnlyMiddleware(BaseMiddleware):
    """Silently discard updates that were not sent by a configured administrator."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not isinstance(user, User) or not is_admin(user.id):
            return None
        return await handler(event, data)
