import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent

from src.bot.handlers.admin import router as admin_router
from src.bot.handlers.start import router as start_router
from src.bot.handlers.subscription import router as subscription_router
from src.common.config import settings

logger = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> None:
    logger.exception("Unhandled bot exception", exc_info=event.exception)


async def run() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required. Fill it in your .env file.")

    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.errors.register(on_error)

    dp.include_router(start_router)
    dp.include_router(subscription_router)
    dp.include_router(admin_router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run())
