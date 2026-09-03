from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.services.order_service import OrderService
from src.backend.services.pool_assignment_service import PoolAssignmentService
from src.backend.services.renewal_planner_service import RenewalPlannerService
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
_manual_vps_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def _with_session(callback):
    db = SessionLocal()
    try:
        return callback(db)
    finally:
        db.close()


def _healthcheck_tick():
    return _with_session(lambda db: NodeOrchestrationService(db).run_healthcheck())


def _capacity_tick():
    return _with_session(lambda db: OrderService(db).ensure_capacity_allocation())


def _assignment_maintenance_tick():
    return _with_session(lambda db: PoolAssignmentService(db).run_maintenance())


def _renewal_tick():
    return _with_session(lambda db: RenewalPlannerService(db).apply())


def _manual_vps_tick():
    if not settings.manual_vps_setup_enabled:
        return {"status": "disabled"}
    from src.backend.services.manual_vps_setup import ManualVpsSetupService
    return _with_session(lambda db: ManualVpsSetupService(db).tick())


async def _manual_vps_runner() -> None:
    while _stop_event and not _stop_event.is_set():
        try:
            setup = await asyncio.to_thread(_manual_vps_tick)
            logger.debug("manual VPS setup tick: %s", setup.get("status"))
        except Exception as exc:
            logger.warning("manual VPS setup tick failed: %s", type(exc).__name__)
        await asyncio.sleep(max(settings.healthcheck_interval_seconds, 10))


async def _runner() -> None:
    next_assignment_maintenance = 0.0
    next_renewal_plan = 0.0
    while _stop_event and not _stop_event.is_set():
        try:
            result = await asyncio.to_thread(_healthcheck_tick)
            logger.debug("scheduled healthcheck tick completed: checked=%s", result.get("checked", 0))
            allocation = await asyncio.to_thread(_capacity_tick)
            logger.debug(
                "scheduled capacity tick completed: status=%s reason=%s",
                allocation.get("status"),
                allocation.get("reason", ""),
            )
            monotonic_now = time.monotonic()
            if monotonic_now >= next_assignment_maintenance:
                maintenance = await asyncio.to_thread(_assignment_maintenance_tick)
                logger.debug("assignment maintenance completed: %s", maintenance)
                next_assignment_maintenance = monotonic_now + max(
                    int(settings.pool_assignment_maintenance_interval_seconds),
                    30,
                )
            if settings.auto_renewal_actions_enabled and monotonic_now >= next_renewal_plan:
                renewal = await asyncio.to_thread(_renewal_tick)
                logger.info("renewal plan applied: actions=%s", len(renewal.get("results", [])))
                next_renewal_plan = monotonic_now + max(
                    int(settings.renewal_planning_interval_seconds),
                    3600,
                )
            # #region agent log
            _debug_log("H1", "healthcheck_scheduler.py:_runner", "tick_ok", {"checked": result.get("checked", 0)})
            # #endregion
        except Exception as exc:
            logger.warning("scheduled healthcheck tick failed: %s: %s", type(exc).__name__, exc)
            # #region agent log
            _debug_log("H1", "healthcheck_scheduler.py:_runner", "tick_failed", {"error": f"{type(exc).__name__}: {exc}"})
            # #endregion
        await asyncio.sleep(max(settings.healthcheck_interval_seconds, 10))


def start_healthcheck_scheduler() -> None:
    global _task, _manual_vps_task, _stop_event
    if _task and not _task.done():
        return
    _stop_event = asyncio.Event()
    _task = asyncio.create_task(_runner())
    if settings.manual_vps_setup_enabled:
        # Installation IO must not delay health checks, assignments or renewals.
        _manual_vps_task = asyncio.create_task(_manual_vps_runner())


async def stop_healthcheck_scheduler() -> None:
    global _task, _manual_vps_task, _stop_event
    if _stop_event:
        _stop_event.set()
    for task in (_task, _manual_vps_task):
        if task:
            with suppress(asyncio.CancelledError):
                task.cancel()
                await task
    _task = None
    _manual_vps_task = None
    _stop_event = None
