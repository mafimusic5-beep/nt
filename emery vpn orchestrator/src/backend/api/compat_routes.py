from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.backend.deps.db import get_db
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)

compat_router = APIRouter()


class AuthKeyRequestBody(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    access_key: str | None = None
    device_id: str | None = None
    device_name: str | None = None
    client_public_key: str | None = None
    timestamp: str | None = None
    nonce: str | None = None
    signature: str | None = None
    signature_algorithm: str | None = None
    client_platform: str | None = None
    app_version: str | None = None


class VpnConnectRequestBody(BaseModel):
    access_key: str = Field(min_length=1, max_length=128)
    server_id: int


def _resolve_subscription_by_key(db: Session, key: str):
    return SubscriptionService(db).resolve_subscription_by_access_key(key)


def _bearer_key(authorization: str = Header(default="")) -> str:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="invalid_or_expired_key")
    return authorization[7:].strip()


def _env_enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str, default: str = "") -> set[str]:
    raw = os.getenv(name, default)
    return {item.strip() for item in raw.replace(";", ",").split(",") if item.strip()}


def _normalize_sha256(value: str) -> str:
    return value.strip().replace(":", "").replace("-", "").upper()


def _request_device_id(request: Request, fallback: str | None = None) -> str:
    return (request.headers.get("x-emery-device-id", "") or fallback or "").strip()


def _request_device_name(request: Request, fallback: str | None = None) -> str:
    value = (fallback or request.headers.get("x-skryon-device-name", "") or "Android Device").strip()
    return value[:80] if value else "Android Device"


def _require_app_integrity(request: Request) -> None:
    """Optional server-side gate for hardened Android clients.

    Enable with:
      SKRYON_REQUIRE_APP_INTEGRITY=1
      SKRYON_ALLOWED_APP_SIGNATURE_SHA256S=<release_cert_sha256>[,<backup_cert_sha256>]

    This does not replace device-bound ECDSA verification. It rejects obvious repacked APKs,
    debug builds and runtime-hooked clients before any VPN config is returned.
    """
    if not _env_enabled("SKRYON_REQUIRE_APP_INTEGRITY", "0"):
        return

    integrity = request.headers.get("x-skryon-app-integrity", "").strip()
    if not hmac.compare_digest(integrity, "ok"):
        raise HTTPException(status_code=403, detail="app_integrity_failed")

    if request.headers.get("x-skryon-app-debug", "").strip().lower() == "true":
        raise HTTPException(status_code=403, detail="debug_build_not_allowed")

    package_name = request.headers.get("x-skryon-app-package", "").strip()
    allowed_packages = _csv_env("SKRYON_ALLOWED_APP_PACKAGES", "com.skryon.shield,com.skryon.shield.fdroid")
    if package_name not in allowed_packages:
        raise HTTPException(status_code=403, detail="app_package_not_allowed")

    allowed_signatures = {_normalize_sha256(value) for value in _csv_env("SKRYON_ALLOWED_APP_SIGNATURE_SHA256S")}
    presented_signatures = {
        _normalize_sha256(value)
        for value in request.headers.get("x-skryon-app-signature-sha256", "").replace(";", ",").split(",")
        if value.strip()
    }
    if allowed_signatures and not (presented_signatures & allowed_signatures):
        raise HTTPException(status_code=403, detail="app_signature_not_allowed")

    if _env_enabled("SKRYON_REJECT_ROOTED_CLIENTS", "1"):
        if request.headers.get("x-skryon-rooted", "").strip().lower() == "true":
            raise HTTPException(status_code=403, detail="rooted_client_not_allowed")

    if _env_enabled("SKRYON_REJECT_EMULATOR_CLIENTS", "0"):
        if request.headers.get("x-skryon-emulator", "").strip().lower() == "true":
            raise HTTPException(status_code=403, detail="emulator_client_not_allowed")


def _register_or_require_device(
    db: Session,
    service: SubscriptionService,
    subscription_id: int,
    device_id: str,
    device_name: str,
):
    if not device_id:
        raise HTTPException(status_code=409, detail="device_fingerprint_required")
    device = service._register_device_inner(subscription_id, device_id, "android", device_name)
    db.commit()
    return device


@compat_router.post("/auth/key")
def auth_key(payload: AuthKeyRequestBody, request: Request, db: Session = Depends(get_db)):
    _require_app_integrity(request)
    access_key = (payload.access_key or payload.key).strip()
    service = SubscriptionService(db)
    code, sub = service.resolve_subscription_by_access_key(access_key)
    if not code or not sub:
        return {"valid": False, "error": "invalid_or_expired_key"}

    device_id = _request_device_id(request, payload.device_id)
    device_name = _request_device_name(request, payload.device_name)
    device = _register_or_require_device(db, service, sub.id, device_id, device_name)
    used = service.repo.count_active_devices(sub.id)

    return {
        "valid": True,
        "vpn_enabled": True,
        "router_enabled": False,
        "expires_at": sub.ends_at,
        "plan_name": sub.plan_code,
        "order_id": str(sub.id),
        "device_id": device.device_fingerprint,
        "device_name": device.device_name,
        "devices_used": used,
        "devices_limit": sub.devices_limit,
    }


@compat_router.get("/profile")
def profile(request: Request, access_key: str = Depends(_bearer_key), db: Session = Depends(get_db)):
    _require_app_integrity(request)
    service = SubscriptionService(db)
    code, sub = service.resolve_subscription_by_access_key(access_key)
    if not code or not sub:
        return {"error": "invalid_or_expired_key"}

    device_id = _request_device_id(request)
    device = service._resolve_device_for_subscription(sub.id, device_id)
    return {
        "user_id": code.user_id,
        "vpn_enabled": True,
        "router_enabled": False,
        "expires_at": sub.ends_at,
        "plan_name": sub.plan_code,
        "device_id": device.device_fingerprint,
        "device_name": device.device_name,
        "devices_used": service.repo.count_active_devices(sub.id),
        "devices_limit": sub.devices_limit,
    }


@compat_router.get("/vpn/config")
def vpn_config(request: Request, access_key: str = Depends(_bearer_key), db: Session = Depends(get_db)):
    _require_app_integrity(request)
    service = SubscriptionService(db)
    code, sub = service.resolve_subscription_by_access_key(access_key)
    if not code or not sub:
        return {"error": "invalid_or_expired_key"}

    device_id = _request_device_id(request)
    device = service._resolve_device_for_subscription(sub.id, device_id)
    orchestrator = NodeOrchestrationService(db)
    try:
        cfg = orchestrator.build_user_config(sub.id, device=device)
        service.audit.write("user", str(code.user_id), "compat_vpn_config_requested", "subscription", str(sub.id))
        db.commit()
        return {"import_text": cfg.get("import_text"), "error": None}
    except HTTPException as exc:
        if exc.detail in {"no_healthy_node", "node_not_found"}:
            raise HTTPException(status_code=404, detail="no_allocation")
        raise


@compat_router.get("/vpn/servers")
def vpn_servers(request: Request, db: Session = Depends(get_db)):
    _require_app_integrity(request)
    return SubscriptionService(db).list_vpn_servers()


@compat_router.post("/vpn/connect")
def vpn_connect(payload: VpnConnectRequestBody, request: Request, db: Session = Depends(get_db)):
    _require_app_integrity(request)
    device_id = _request_device_id(request)
    try:
        return SubscriptionService(db).connect_to_server(
            payload.access_key,
            payload.server_id,
            device_fingerprint=device_id,
        )
    except HTTPException as exc:
        if exc.detail == "server_config_unavailable":
            return {"error": "server_config_unavailable"}
        raise
