#!/usr/bin/env python3
"""Surgical live-bot integration. Keep pre-existing production hotfixes intact."""
from __future__ import annotations

import argparse
from pathlib import Path
import re

IMPORTS = (
    "from src.bot.handlers.windows_releases import router as windows_releases_router",
    "from src.bot.handlers.admin_guided import router as admin_guided_router",
)

def edit_main(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    marker = "from src.bot.handlers.admin import router as admin_router"
    if text.count(marker) != 1:
        raise RuntimeError("Bot main import location differs; refusing to patch.")
    for line in IMPORTS:
        if line not in text:
            text = text.replace(marker, line + "\n" + marker, 1)
    route_marker = "    dp.include_router(admin_router)"
    if text.count(route_marker) != 1:
        raise RuntimeError("Bot router registration differs; refusing to patch.")
    for name in ("windows_releases_router", "admin_guided_router"):
        registration = "    dp.include_router(" + name + ")"
        if registration not in text:
            text = text.replace(route_marker, registration + "\n" + route_marker, 1)
    # Only activate local-Bot-API wiring when its env var is explicitly set.
    bot_marker = "    bot = Bot(token=settings.bot_token)"
    if bot_marker in text and "SKRYON_TELEGRAM_LOCAL_API_URL" not in text:
        local_impl = """    # Local Telegram Bot API is needed for documents >20 MB.
    # Cloud polling remains unchanged until explicitly configured.
    import os
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer
    local_url = os.getenv("SKRYON_TELEGRAM_LOCAL_API_URL", "").strip()
    if local_url:
        bot = Bot(token=settings.bot_token, session=AiohttpSession(
            api=TelegramAPIServer.from_base(local_url.rstrip("/"), is_local=True)))
    else:
        bot = Bot(token=settings.bot_token)"""
        text = text.replace(bot_marker, local_impl, 1)
    path.write_text(text, encoding="utf-8")

def edit_keyboard(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    marker = re.search(r"(?m)^def admin_menu_keyboard\b.*:", text)
    if not marker:
        raise RuntimeError("Admin keyboard function missing; refusing to patch.")
    next_function = re.search(r"(?m)^def\s+", text[marker.end():])
    end = marker.end() + next_function.start() if next_function else len(text)
    piece = text[marker.start():end]
    insert_before = re.search(r"(?m)^    kb\.adjust\(", piece)
    if not insert_before:
        raise RuntimeError("Admin keyboard builder missing; refusing to patch.")
    buttons = (
        ("📦 Релизы Windows", "admin_releases"),
        ("🖥 Управление VPS", "admin_node_tools"),
        ("➕ Добавить VPS", "admin_vps_add"),
        ("📚 Помощь по админке", "admin_help"),
    )
    new_buttons = ""
    for caption, callback in buttons:
        if f'callback_data="{callback}"' not in piece:
            new_buttons += f'    kb.button(text="{caption}", callback_data="{callback}")\n'
    if new_buttons:
        pos = insert_before.start()
        piece = piece[:pos] + new_buttons + piece[pos:]
    text = text[:marker.start()] + piece + text[end:]
    path.write_text(text, encoding="utf-8")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("site_root")
    args = parser.parse_args()
    root = Path(args.site_root)
    edit_main(root / "src/bot/main.py")
    edit_keyboard(root / "src/bot/ui/keyboards.py")
    print("LIVE_ADMIN_MENU_PATCHED")

if __name__ == "__main__":
    main()
