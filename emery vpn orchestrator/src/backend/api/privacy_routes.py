from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from src.backend.deps.db import get_db
from src.backend.schemas.subscription import (
    VpnRegionConnectRequest,
    VpnRegionConnectResponse,
    VpnRegionItemResponse,
)
from src.backend.services.subscription_service import (
    SubscriptionService,
    _sanitize_public_import_text,
    _sanitize_public_server_label,
)
from src.backend.services.traffic_policy_service import TrafficPolicyService
from src.backend.utils.app_version import APP_VERSION_HEADER, app_update_required, app_update_server_placeholder

router = APIRouter(prefix="/api/v1")


@router.get("/vpn/regions", response_model=list[VpnRegionItemResponse])
def list_vpn_regions(
    x_skryon_app_version_code: int = Header(default=0, alias=APP_VERSION_HEADER),
    db: Session = Depends(get_db),
):
    if app_update_required(x_skryon_app_version_code):
        placeholder = app_update_server_placeholder()
        return [
            VpnRegionItemResponse(
                region_code="update",
                name=str(placeholder.get("city") or "Требуется обновление"),
                is_available=False,
            )
        ]

    rows = SubscriptionService(db).node_orchestrator.list_region_entries()
    result: list[VpnRegionItemResponse] = []
    for row in rows:
        code = str(row.get("region_code") or "").strip().lower()
        if not code:
            continue
        name = _sanitize_public_server_label(
            str(row.get("region_name") or row.get("city") or code.upper()),
            fallback=code.upper(),
        )
        result.append(
            VpnRegionItemResponse(
                region_code=code,
                name=name,
                is_available=bool(row.get("is_available", False)),
            )
        )
    return result


@router.post("/vpn/connect-region", response_model=VpnRegionConnectResponse)
def connect_vpn_region(
    payload: VpnRegionConnectRequest,
    x_emery_device_id: str = Header(default="", alias="X-Emery-Device-Id"),
    db: Session = Depends(get_db),
):
    service = SubscriptionService(db)
    code, sub = service.resolve_subscription_by_access_key(payload.access_key)
    if not code or not sub:
        raise HTTPException(status_code=401, detail="invalid_or_expired_key")

    region_code = payload.region_code.strip().lower()
    device = service._resolve_device_for_subscription(sub.id, x_emery_device_id or None)
    if service._unique_assignment_enabled():
        cfg = service._build_unique_device_config(sub, device, region_code)
        selected_region = str(cfg.get("region_code") or region_code).strip().lower()
    else:
        cfg = service.node_orchestrator.build_user_config_for_region(sub.id, region_code, device)
        selected_region = str(cfg["node"].region_code or region_code).strip().lower()

    if selected_region != region_code:
        raise HTTPException(status_code=409, detail="region_mismatch")

    public_import_text = _sanitize_public_import_text(str(cfg.get("import_text") or ""))
    if not public_import_text:
        raise HTTPException(status_code=409, detail="server_config_unavailable")

    TrafficPolicyService(db).apply_from_import_text(public_import_text, payload.traffic_policy)
    service.audit.write(
        "user",
        str(code.user_id),
        "vpn_connect_region_requested",
        "vpn_region",
        region_code,
    )
    db.commit()
    return VpnRegionConnectResponse(
        region_code=region_code,
        name=region_code.upper(),
        import_text=public_import_text,
    )
