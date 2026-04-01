from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.common.config import settings
from src.common.db import SessionLocal

logger = logging.getLogger(__name__)


# #region agent log
def _debug_log(hypothesis_id: str, location: str, message: str, data: dict | None = None) -> None:
    import json as _json, time as _time, uuid as _uuid, pathlib as _pathlib
    try:
        entry = _json.dumps({
            "sessionId": "2ca983", "id": f"log_{_uuid.uuid4()}", "runId": "run1",
            "hypothesisId": hypothesis_id, "location": location,
            "message": message, "data": data or {}, "timestamp": int(_time.time() * 1000),
        }, ensure_ascii=False)
        _pathlib.Path("debug-2ca983.log").open("a", encoding="utf-8").write(entry + "\n")
    except Exception:
        pass
# #endregion

_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


async def _runner() -> None:
    while _stop_event and not _stop_event.is_set():
        db = SessionLocal()
        try:
            result = await asyncio.to_thread(NodeOrchestrationService(db).run_healthcheck)
            logger.debug("scheduled healthcheck tick completed: checked=%s", result.get("checked", 0))
            # #region agent log
            _debug_log("H1", "healthcheck_scheduler.py:_runner", "tick_ok", {"checked": result.get("checked", 0)})
            # #endregion
        except Exception as exc:
            logger.warning("scheduled healthcheck tick failed: %s: %s", type(exc).__name__, exc)
            # #region agent log
            _debug_log("H1", "healthcheck_scheduler.py:_runner", "tick_failed", {"error": f"{type(exc).__name__}: {exc}"})
            # #endregion
        finally:
            db.close()
        await asyncio.sleep(max(settings.healthcheck_interval_seconds, 10))


def start_healthcheck_scheduler() -> None:
    global _task, _stop_event
    if _task and not _task.done():
        return
    _stop_event = asyncio.Event()
    _task = asyncio.create_task(_runner())


async def stop_healthcheck_scheduler() -> None:
    global _task, _stop_event
    if _stop_event:
        _stop_event.set()
    if _task:
        with suppress(asyncio.CancelledError):
            _task.cancel()
            await _task
    _task = None
    _stop_event = None
