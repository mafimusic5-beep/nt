from __future__ import annotations

import json
import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from src.backend.services.pool_assignment_service import PoolAssignmentService
from src.backend.services.xray_credential_service import VlessDeviceConfigBuilder
from src.common.config import settings
from src.common.db import SessionLocal
from src.common.models import VpnNode

logger = logging.getLogger(__name__)


def _nodes_for_region(db, region_code: str) -> list[VpnNode]:
    stmt = select(VpnNode)
    if region_code and region_code != "auto":
        stmt = stmt.where(VpnNode.region_code == region_code)
    return list(db.scalars(stmt).all())


def diagnose_capacity_failure_in_session(db, region_code: str = "auto") -> str:
    """Return a safe, stable reason code without exposing node secrets."""

    nodes = _nodes_for_region(db, region_code)
    if not nodes:
        return "pool_region_unavailable" if region_code != "auto" else "pool_no_nodes"

    active = [node for node in nodes if node.status == "active"]
    if not active:
        return "pool_no_active_nodes"

    healthy = [
        node
        for node in active
        if node.health_status in {"healthy", "degraded"}
    ]
    if not healthy:
        return "pool_no_healthy_nodes"

    with_capacity = [
        node
        for node in healthy
        if int(node.current_clients or 0) < int(node.capacity_clients or 0)
    ]
    if not with_capacity:
        return "pool_capacity_full"

    gate_ready = with_capacity
    if settings.device_bound_gate_enabled:
        gate_ready = [
            node
            for node in with_capacity
            if VlessDeviceConfigBuilder.gate_endpoint(node) is not None
        ]
        if not gate_ready:
            return "pool_gate_not_ready"

    assignment_service = PoolAssignmentService(db)
    if gate_ready and all(assignment_service._free_port(node.id) is None for node in gate_ready):
        return "pool_ports_exhausted"

    # If nodes survived every deterministic filter, the failed admission was
    # most likely caused by a concurrent counter/port claim. A retry is safe.
    return "pool_assignment_race"


def diagnose_capacity_failure(region_code: str = "auto") -> str:
    db = SessionLocal()
    try:
        return diagnose_capacity_failure_in_session(db, region_code)
    except Exception:  # noqa: BLE001
        logger.exception("capacity diagnostic failed")
        return "server_capacity_unavailable"
    finally:
        db.close()


async def _region_from_request(request: Request) -> str:
    try:
        raw = await request.body()
        if not raw:
            return "auto"
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return "auto"
        region = str(payload.get("region_code") or "auto").strip().lower()
        return region or "auto"
    except Exception:  # noqa: BLE001
        return "auto"


async def http_exception_with_diagnostics(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if detail == "server_capacity_unavailable":
        region = await _region_from_request(request)
        detail = diagnose_capacity_failure(region)
        logger.warning(
            "pool admission rejected: reason=%s region=%s path=%s",
            detail,
            region,
            request.url.path,
        )

    headers = dict(exc.headers or {})
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail},
        headers=headers,
    )
