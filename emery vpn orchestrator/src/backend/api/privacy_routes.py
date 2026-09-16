from __future__ import annotations

import zlib

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from src.backend.deps.db import get_db
from src.backend.schemas.subscription import (
    VpnConnectRequest,
    VpnConnectResponse,
    VpnRegionConnectRequest,
    VpnRegionConnectResponse,
    VpnRegionItemResponse,
    VpnServerItemResponse,
)
from src.backend.services.subscription_service import (
    SubscriptionService,
    _sanitize_public_import_text,
    _sanitize_public_server_label,
)
from src.backend.services.traffic_policy_service import TrafficPolicyService
from src.backend.utils.app_version import APP_VERSION_HEADER, app_update_required, app_update_server_placeholder
from src.common.models import VpnNode

router = APIRouter(prefix="/api/v1")

_LOGICAL_REGION_ID_BASE = 1_500_000_000
_LOGICAL_REGION_ID_MASK = 0x1FFFFFFF


def logical_region_id(region_code: str) -> int:
    code = region_code.strip().lower()
    value = zlib.crc32(code.encode("utf-8")) & _LOGICAL_REGION_ID_MASK
    return _LOGICAL_REGION_ID_BASE + value


def _public_regions(service: SubscriptionService) -> list[dict]:
    result: list[dict] = []
    used_ids: dict[int, str] = {}
    for row in service.node_orchestrator.list_region_entries():
        code = str(row.get("region_code") or "").strip().lower()
        if not code:
            continue
        public_id = logical_region_id(code)
        previous = used_ids.get(public_id)
        if previous is not None and previous != code:
            raise HTTPException(status_code=503, detail="region_identifier_collision")
        used_ids[public_id] = code
        name = _sanitize_public_server_label(
            str(row.get("region_name") or row.get("city") or code.upper()),
            fallback=code.upper(),
        )
        result.append(
            {
                "id": public_id,
                "region_code": code,
                "name": name,
                "health_status": str(row.get("health_status") or "unknown"),
                "is_available": bool(row.get("is_available", False)),
            }
        )
    return result


def _resolve_requested_region(service: SubscriptionService, public_id: int) -> str | None:
    for row in _public_regions(service):
        if row["id"] == public_id:
            return str(row["region_code"])
    return None


def _connect_region(service: SubscriptionService, sub, device, region_code: str) -> tuple[dict, str]:
    normalized = region_code.strip().lower()
    if service._unique_assignment_enabled():
        cfg = service._build_unique_device_config(sub, device, normalized)
        selected_region = str(cfg.get("region_code") or normalized).strip().lower()
    else:
        cfg = service.node_orchestrator.build_user_config_for_region(sub.id, normalized, device)
        selected_region = str(cfg["node"].region_code or normalized).strip().lower()
    if selected_region != normalized:
        raise HTTPException(status_code=409, detail="region_mismatch")
    return cfg, selected_region


def _finish_connect(
    *,
    service: SubscriptionService,
    code,
    cfg: dict,
    region_code: str,
    traffic_policy: str,
) -> str:
    public_import_text = _sanitize_public_import_text(str(cfg.get("import_text") or ""))
    if not public_import_text:
        raise HTTPException(status_code=409, detail="server_config_unavailable")
    TrafficPolicyService(service.db).apply_from_import_text(public_import_text, traffic_policy)
    service.audit.write(
        "user",
        str(code.user_id),
        "vpn_connect_region_requested",
        "vpn_region",
        region_code,
    )
    service.db.commit()
    return public_import_text


# Privacy-preserving replacement for the legacy /api/v1/vpn/servers route.
# It intentionally keeps the exact old response shape so installed Android builds
# keep working, but "id" is a stable logical-region token rather than VpnNode.id.
@router.get("/vpn/servers", response_model=list[VpnServerItemResponse])
def list_vpn_servers_private(
    x_skryon_app_version_code: int = Header(default=0, alias=APP_VERSION_HEADER),
    db: Session = Depends(get_db),
):
    if app_update_required(x_skryon_app_version_code):
        return [app_update_server_placeholder()]
    return [
        {
            "id": row["id"],
            "city": row["name"],
            "health_status": row["health_status"],
            "is_available": row["is_available"],
        }
        for row in _public_regions(SubscriptionService(db))
    ]


# Privacy-preserving replacement for the legacy /api/v1/vpn/connect route.
# New list responses use logical IDs. Cached physical IDs from older clients remain
# accepted as a compatibility fallback and are never required by new clients.
@router.post("/vpn/connect", response_model=VpnConnectResponse)
def connect_vpn_server_private(
    payload: VpnConnectRequest,
    x_emery_device_id: str = Header(default="", alias="X-Emery-Device-Id"),
    db: Session = Depends(get_db),
):
    service = SubscriptionService(db)
    code, sub = service.resolve_subscription_by_access_key(payload.access_key)
    if not code or not sub:
        raise HTTPException(status_code=401, detail="invalid_or_expired_key")

    device = service._resolve_device_for_subscription(sub.id, x_emery_device_id or None)
    region_code = _resolve_requested_region(service, payload.server_id)
    response_server_id = payload.server_id

    if region_code is not None:
        cfg, selected_region = _connect_region(service, sub, device, region_code)
    else:
        # Compatibility for a client that cached a physical ID before this rollout.
        requested_node = db.get(VpnNode, payload.server_id)
        if requested_node is None:
            raise HTTPException(status_code=404, detail="server_not_found")
        selected_region = str(requested_node.region_code or "").strip().lower()
        cfg, selected_region = _connect_region(service, sub, device, selected_region)

    public_import_text = _finish_connect(
        service=service,
        code=code,
        cfg=cfg,
        region_code=selected_region,
        traffic_policy=payload.traffic_policy,
    )
    return VpnConnectResponse(
        server_id=response_server_id,
        city=selected_region,
        import_text=public_import_text,
    )


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
    return [
        VpnRegionItemResponse(
            region_code=str(row["region_code"]),
            name=str(row["name"]),
            is_available=bool(row["is_available"]),
        )
        for row in _public_regions(SubscriptionService(db))
    ]


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

    device = service._resolve_device_for_subscription(sub.id, x_emery_device_id or None)
    cfg, selected_region = _connect_region(service, sub, device, payload.region_code)
    public_import_text = _finish_connect(
        service=service,
        code=code,
        cfg=cfg,
        region_code=selected_region,
        traffic_policy=payload.traffic_policy,
    )
    return VpnRegionConnectResponse(
        region_code=selected_region,
        name=selected_region.upper(),
        import_text=public_import_text,
    )
