#!/usr/bin/env python3
"""One-time, deliberately explicit cloud -> local Telegram Bot API migration.

Must run as root on the LIVE bot host only, after prepare_local_telegram_api.sh.
Never embeds/prints the bot token or Telegram API ID/hash.
If migration fails after logOut, the cloud endpoint may refuse bot re-login
for TEN MINUTES. The local service remains running to allow manual recovery.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error

LIVE = Path("/opt/nt/emery vpn orchestrator")
BOT_SERVICE = "skryon-bot.service"
API_SERVICE = "telegram-bot-api.service"
DROPIN_DIR = Path("/etc/systemd/system/skryon-bot.service.d")
DROPIN = DROPIN_DIR / "95-local-telegram-api.conf"
EXPECTED_URL = "http://127.0.0.1:8081"


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=30)


def token_from_local_config() -> str:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        from dotenv import dotenv_values
        token = str(dotenv_values(LIVE / ".env").get("BOT_TOKEN") or "").strip()
    if not token or ":" not in token or len(token) < 20:
        raise RuntimeError("BOT_TOKEN missing from private production configuration")
    return token


def call_bot_api(base: str, token: str, operation: str) -> dict:
    request = urllib.request.Request(
        f"{base}/bot{token}/{operation}",
        data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read(8192)
    return json.loads(body)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--check", "--switch"):
        raise SystemExit("Usage: switch_bot_to_local_api.py --check | --switch")
    if os.geteuid() != 0:
        raise SystemExit("ROOT_REQUIRED")
    if not (LIVE / "src/bot/main.py").is_file():
        raise SystemExit("BOT_HOST_NOT_RECOGNIZED")
    source = (LIVE / "src/bot/main.py").read_text(encoding="utf-8")
    if "SKRYON_TELEGRAM_LOCAL_API_URL" not in source:
        raise SystemExit("BOT_CLIENT_DOES_NOT_SUPPORT_LOCAL_API")
    if not Path("/etc/systemd/system/telegram-bot-api.service").is_file():
        raise SystemExit("LOCAL_API_SYSTEMD_UNIT_MISSING")
    if run("systemctl", "is-active", "--quiet", API_SERVICE, check=False).returncode:
        raise SystemExit("LOCAL_TELEGRAM_API_NOT_RUNNING")
    if run("systemctl", "is-active", "--quiet", BOT_SERVICE, check=False).returncode:
        raise SystemExit("PRODUCTION_BOT_NOT_RUNNING")
    if DROPIN.exists():
        raise SystemExit("EXISTING_BOT_LOCAL_API_DROPIN_NEEDS_MANUAL_REVIEW")
    # Restrict the endpoint to 127.0.0.1. Reject port forwarding or remote addresses.
    for line in run("ss", "-lnt", "( sport = :8081 )").stdout.splitlines():
        if ":8081" in line and "127.0.0.1:8081" not in line:
            raise SystemExit("LOCAL_BOT_API_NOT_LOOPBACK_ONLY")
    token = token_from_local_config()
    if sys.argv[1] == "--check":
        print("READY_TO_SWITCH_REQUIRES_MANUAL_--switch")
        print("WARNING_CLOUD_LOGOUT_RESTRICTS_RELOGIN_FOR_10_MINUTES")
        return
    print("STOPPING_EXISTING_BOT")
    run("systemctl", "stop", BOT_SERVICE)
    try:
        cloud = call_bot_api("https://api.telegram.org", token, "logOut")
        if not cloud.get("ok") or not cloud.get("result"):
            raise RuntimeError("Cloud Bot API logOut returned false")
        print("CLOUD_LOGOUT_CONFIRMED")
        DROPIN_DIR.mkdir(parents=True, exist_ok=True)
        DROPIN.write_text(
            "[Service]\nEnvironment=SKRYON_TELEGRAM_LOCAL_API_URL="
            + EXPECTED_URL + "\n", encoding="ascii")
        DROPIN.chmod(0o644)
        run("systemctl", "daemon-reload")
        run("systemctl", "start", BOT_SERVICE)
        time.sleep(4)
        if run("systemctl", "is-active", "--quiet", BOT_SERVICE, check=False).returncode:
            raise RuntimeError("Bot failed to start with local API")
        result = call_bot_api(EXPECTED_URL, token, "getMe")
        if not result.get("ok"):
            raise RuntimeError("Local Bot API getMe failed")
        print("BOT_NOW_USES_LOCAL_TELEGRAM_API")
        print("SAFE_TO_SEND_LARGE_EXE_TO_OWNER_ADMIN_CHAT")
    except Exception:
        # Cloud Bot API may reject access for 10 minutes after logOut.
        # Do NOT silently restart the old cloud bot with a competing poller.
        print("MIGRATION_NEEDS_MANUAL_RECOVERY")
        print("LOCAL_SERVER_AND_BOT_UNIT_LEFT_IN_PLACE")
        raise


if __name__ == "__main__":
    main()
