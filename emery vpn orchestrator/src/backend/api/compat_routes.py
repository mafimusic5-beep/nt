from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.backend.deps.db import get_db
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.services.subscription_service import SubscriptionService
from src.backend.utils.app_version import (
    APP_VERSION_HEADER,
    app_update_metadata,
    app_update_required,
    app_update_server_placeholder,
)
from src.common.config import settings

logger = logging.getLogger(__name__)

compat_router = APIRouter()


class AuthKeyRequestBody(BaseModel):
    key: str = Field(min_length=1, max_length=128)


class VpnConnectRequestBody(BaseModel):
    access_key: str = Field(min_length=1, max_length=128)
    server_id: int


def _resolve_subscription_by_key(db: Session, key: str):
    return SubscriptionService(db).resolve_subscription_by_access_key(key)


def _bearer_key(authorization: str = Header(default="")) -> str:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="invalid_or_expired_key")
    return authorization[7:].strip()


def _require_active_subscription(access_key: str, db: Session) -> SubscriptionService:
    service = SubscriptionService(db)
    code, sub = service.resolve_subscription_by_access_key(access_key)
    if not code or not sub:
        raise HTTPException(status_code=401, detail="invalid_or_expired_key")
    return service


def _row_value(row, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _region_revision_snapshot(db: Session) -> tuple[str, int]:
    rows = SubscriptionService(db).list_vpn_servers()
    normalized = [
        {
            "id": str(_row_value(row, "id", "")),
            "city": str(_row_value(row, "city", _row_value(row, "region_code", ""))),
            "health_status": str(_row_value(row, "health_status", "")),
            "is_available": bool(_row_value(row, "is_available", True)),
        }
        for row in rows
    ]
    normalized.sort(key=lambda item: (item["city"], item["id"]))
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), len(normalized)


@compat_router.post("/auth/key")
def auth_key(
    payload: AuthKeyRequestBody,
    x_skryon_app_version_code: int = Header(default=0, alias=APP_VERSION_HEADER),
    db: Session = Depends(get_db),
):
    code, sub = _resolve_subscription_by_key(db, payload.key)
    if not code or not sub:
        return {"valid": False, "error": "invalid_or_expired_key"}
    update_required = app_update_required(x_skryon_app_version_code)
    result = {
        "valid": True,
        "vpn_enabled": not update_required,
        "router_enabled": False,
        "expires_at": sub.ends_at,
        "plan_name": settings.app_update_message if update_required else sub.plan_code,
        "order_id": str(sub.id),
    }
    if update_required:
        result.update(app_update_metadata())
    return result


@compat_router.get("/profile")
def profile(
    access_key: str = Depends(_bearer_key),
    x_skryon_app_version_code: int = Header(default=0, alias=APP_VERSION_HEADER),
    db: Session = Depends(get_db),
):
    code, sub = _resolve_subscription_by_key(db, access_key)
    if not code or not sub:
        return {"error": "invalid_or_expired_key"}
    update_required = app_update_required(x_skryon_app_version_code)
    result = {
        "user_id": code.user_id,
        "vpn_enabled": not update_required,
        "router_enabled": False,
        "expires_at": sub.ends_at,
        "plan_name": settings.app_update_message if update_required else sub.plan_code,
    }
    if update_required:
        result.update(app_update_metadata())
    return result


@compat_router.get("/vpn/config")
def vpn_config(access_key: str = Depends(_bearer_key), db: Session = Depends(get_db)):
    code, sub = _resolve_subscription_by_key(db, access_key)
    if not code or not sub:
        return {"error": "invalid_or_expired_key"}
    orchestrator = NodeOrchestrationService(db)
    try:
        cfg = orchestrator.build_user_config(sub.id, device=None)
        return {"import_text": cfg.get("import_text"), "error": None}
    except HTTPException as exc:
        if exc.detail in {"no_healthy_node", "node_not_found"}:
            raise HTTPException(status_code=404, detail="no_allocation")
        raise


@compat_router.get("/vpn/servers")
def vpn_servers(
    x_skryon_app_version_code: int = Header(default=0, alias=APP_VERSION_HEADER),
    db: Session = Depends(get_db),
):
    if app_update_required(x_skryon_app_version_code):
        return [app_update_server_placeholder()]
    return SubscriptionService(db).list_vpn_servers()


@compat_router.get("/api/v1/vpn/regions/revision")
@compat_router.get("/vpn/regions/revision")
def vpn_regions_revision(access_key: str = Depends(_bearer_key), db: Session = Depends(get_db)):
    _require_active_subscription(access_key, db)
    revision, server_count = _region_revision_snapshot(db)
    return {"revision": revision, "server_count": server_count}


@compat_router.get("/api/v1/vpn/regions/events")
@compat_router.get("/vpn/regions/events")
async def vpn_regions_events(
    since: str = "",
    access_key: str = Depends(_bearer_key),
    db: Session = Depends(get_db),
):
    _require_active_subscription(access_key, db)
    wait_seconds = max(5.0, min(float(os.getenv("SKRYON_REGION_EVENT_WAIT_SECONDS", "55")), 70.0))
    tick_seconds = max(0.25, min(float(os.getenv("SKRYON_REGION_EVENT_TICK_SECONDS", "1")), 5.0))
    deadline = asyncio.get_running_loop().time() + wait_seconds

    while True:
        db.rollback()
        revision, server_count = _region_revision_snapshot(db)
        if not since or revision != since:
            return {"changed": True, "revision": revision, "server_count": server_count}
        if asyncio.get_running_loop().time() >= deadline:
            return {"changed": False, "revision": revision, "server_count": server_count}
        await asyncio.sleep(tick_seconds)


@compat_router.post("/vpn/connect")
def vpn_connect(payload: VpnConnectRequestBody, db: Session = Depends(get_db)):
    try:
        return SubscriptionService(db).connect_to_server(payload.access_key, payload.server_id)
    except HTTPException as exc:
        if exc.detail == "server_config_unavailable":
            return {"error": "server_config_unavailable"}
        raise
