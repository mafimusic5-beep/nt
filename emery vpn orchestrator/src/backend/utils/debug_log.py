"""Small NDJSON debug logger for runtime hypothesis validation."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

LOG_FILE = Path("debug-723bf4.log")
SESSION_ID = "723bf4"


def agent_log(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "run1") -> None:
    payload = {
        "sessionId": SESSION_ID,
        "id": f"log_{uuid.uuid4()}",
        "timestamp": int(time.time() * 1000),
        "location": location,
        "message": message,
        "data": data,
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
