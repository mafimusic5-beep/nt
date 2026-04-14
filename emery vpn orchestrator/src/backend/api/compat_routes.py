from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.backend.deps.db import get_db
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)

compat_router = APIRouter()


class AuthKeyRequestBody(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    device_id: str | None = Field(default=None, min_length=6, max_length=128)
    device_name: str = ""
    client_platform: str = "android"


class VpnConnectRequestBody(BaseModel):
    access_key: str = Field(min_length=1, max_length=128)
    server_id: int
    device_fingerprint: str | None = Field(default=None, min_length=6, max_length=128)
    platform: str = "android"
    device_name: str = ""


def _resolve_subscription_by_key(db: Session, key: str):
    return SubscriptionService(db).resolve_subscription_by_access_key(key)


def _bearer_key(authorization: str = Header(default="")) -> str:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="invalid_or_expired_key")
    return authorization[7:].strip()


@compat_router.post("/auth/key")
def auth_key(payload: AuthKeyRequestBody, db: Session = Depends(get_db)):
    service = SubscriptionService(db)
    code, sub = service.resolve_subscription_by_access_key(payload.key)
    if not code or not sub:
        return {"valid": False, "error": "invalid_or_expired_key"}
    if payload.device_id:
        try:
            service._register_device_inner(sub.id, payload.device_id, payload.client_platform, payload.device_name)
            db.commit()
        except HTTPException as exc:
            if exc.status_code == 409 and exc.detail == "device_limit_reached":
                raise HTTPException(status_code=409, detail="device_limit_reached")
            raise
    return {
        "valid": True,
        "vpn_enabled": True,
        "router_enabled": False,
        "expires_at": sub.ends_at,
        "plan_name": sub.plan_code,
        "order_id": str(sub.id),
        "device_id": payload.device_id or "",
        "device_name": payload.device_name,
    }


@compat_router.get("/profile")
def profile(access_key: str = Depends(_bearer_key), db: Session = Depends(get_db)):
    code, sub = _resolve_subscription_by_key(db, access_key)
    if not code or not sub:
        return {"error": "invalid_or_expired_key"}
    return {
        "user_id": code.user_id,
        "vpn_enabled": True,
        "router_enabled": False,
        "expires_at": sub.ends_at,
        "plan_name": sub.plan_code,
    }


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
def vpn_servers(db: Session = Depends(get_db)):
    return SubscriptionService(db).list_vpn_servers()


@compat_router.post("/vpn/connect")
def vpn_connect(payload: VpnConnectRequestBody, db: Session = Depends(get_db)):
    try:
        return SubscriptionService(db).connect_to_server(
            payload.access_key,
            payload.server_id,
            payload.device_fingerprint,
            payload.platform,
            payload.device_name,
        )
    except HTTPException as exc:
        if exc.detail == "server_config_unavailable":
            return {"error": "server_config_unavailable"}
        raise
