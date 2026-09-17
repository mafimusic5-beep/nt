"""Opt-in NDJSON debug logger for short-lived runtime diagnostics.

Privacy rule: production does not create runtime evidence files unless an
operator explicitly enables SKRYON_DEBUG_EVIDENCE. Even then, common user and
credential identifiers are redacted before anything reaches disk.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

LOG_FILE = Path("debug-723bf4.log")
SESSION_ID = "723bf4"
_ENABLED = os.getenv("SKRYON_DEBUG_EVIDENCE", "").strip().lower() in {"1", "true", "yes"}
_SENSITIVE_MARKERS = (
    "access_key",
    "activation",
    "auth",
    "code",
    "device_id",
    "device_fingerprint",
    "fingerprint",
    "ip",
    "nonce",
    "public_key",
    "signature",
    "telegram_id",
    "token",
    "user_id",
)


def _redact(value: Any, key: str = "") -> Any:
    normalized_key = key.strip().lower()
    if any(marker in normalized_key for marker in _SENSITIVE_MARKERS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def agent_log(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "run1") -> None:
    if not _ENABLED:
        return

    payload = {
        "sessionId": SESSION_ID,
        "id": f"log_{uuid.uuid4()}",
        "timestamp": int(time.time() * 1000),
        "location": location,
        "message": message,
        "data": _redact(data),
        "runId": run_id,
        "hypothesisId": hypothesis_id,
    }
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # Debug logging must never break runtime flow.
        return
