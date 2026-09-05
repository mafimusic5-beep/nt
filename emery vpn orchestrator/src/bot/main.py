import asyncio
import logging
import subprocess

from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent

from src.bot.handlers.admin import router as admin_router
from src.bot.handlers.admin_codes import router as admin_codes_router
from src.bot.handlers.server_setup import router as server_setup_router
from src.bot.handlers.start import router as start_router
from src.bot.handlers.subscription import router as subscription_router
from src.common.config import settings

logger = logging.getLogger(__name__)


def _disable_legacy_polling_service() -> None:
    """Keep exactly one Telegram long-polling process on production hosts.

    The legacy skryon-admin-bot service uses the same bot token and can steal
    updates or trigger Telegram getUpdates conflicts. Modern bot startup is the
    final guardrail: if systemd is available, stop and disable the legacy unit.
    Local/dev environments without systemd simply skip this step.
    """
    try:
        result = subprocess.run(
            ["systemctl", "disable", "--now", "skryon-admin-bot.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    if result.returncode == 0:
        logger.info("legacy Telegram polling service disabled")


async def on_error(event: ErrorEvent) -> None:
    logger.exception("Unhandled bot exception", exc_info=event.exception)


async def run() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required. Fill it in your .env file.")
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    await asyncio.to_thread(_disable_legacy_polling_service)

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.errors.register(on_error)
    dp.include_router(start_router)
    dp.include_router(subscription_router)
    dp.include_router(admin_codes_router)
    dp.include_router(server_setup_router)
    dp.include_router(admin_router)
    # Make command updates independent tasks explicitly. Long-running admin
    # operations (notably VPS provisioning) must never serialize all Telegram
    # updates behind one handler.
    await dp.start_polling(bot, handle_as_tasks=True)


if __name__ == "__main__":
    asyncio.run(run())
