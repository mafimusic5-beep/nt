import hashlib
import hmac
import logging
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.subscription_repo import SubscriptionRepository
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.services.pool_assignment_service import PoolAssignmentService
from src.backend.schemas.pool_bridge import (
    PoolReservationConfirmRequest,
    PoolReservationPrepareRequest,
)
from src.backend.utils.debug_log import agent_log
from src.backend.schemas.subscription import (
    HeartbeatRequest,
    RedeemActivationCodeRequest,
    RedeemActivationCodeResponse,
    RegisterDeviceRequest,
    RegisterDeviceResponse,
    SubscriptionStatusResponse,
    UnbindDeviceRequest,
    UserCodeResponse,
    UserDeviceResponse,
    VpnConfigResponse,
    VpnConnectRequest,
    VpnConnectResponse,
    VpnServerItemResponse,
)
from src.backend.utils.security import hash_activation_code
from src.common.config import settings
from src.common.models import VpnNode

logger = logging.getLogger(__name__)

_PUBLIC_PROVIDER_MARKERS = (
    "firstvds",
    "ionos",
    "vps1dollar",
    "data center",
    "datacenter",
    "hosting",
    "hoster",
    "vps",
)


def _contains_public_provider_marker(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in _PUBLIC_PROVIDER_MARKERS)


def _sanitize_public_server_label(value: str, fallback: str = "Server") -> str:
    label = str(value or "").strip()
    fallback_label = str(fallback or "Server").strip() or "Server"
    if not label:
        return fallback_label
    if not _contains_public_provider_marker(label):
        return label

    parenthesized = re.search(r"\(([^()]{2,64})\)\s*$", label)
    if parenthesized:
        candidate = parenthesized.group(1).strip()
        if candidate and not _contains_public_provider_marker(candidate):
            return candidate

    cleaned = label
    for marker in _PUBLIC_PROVIDER_MARKERS:
        cleaned = re.sub(re.escape(marker), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\s_\-:()]+", " ", cleaned).strip()
    cleaned = re.sub(r"\b\d{4,}\b", "", cleaned).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if cleaned and not _contains_public_provider_marker(cleaned):
        return cleaned
    return fallback_label


def _sanitize_public_import_text(import_text: str) -> str:
    """Keep VPN transport fields intact and replace only the client-visible VLESS remark."""
    lines: list[str] = []
    for raw_line in str(import_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("vless://"):
            line = f"{line.split('#', 1)[0]}#Server"
        lines.append(line)
    return "\n".join(lines)


class SubscriptionService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = SubscriptionRepository(db)
        self.audit = AuditRepository(db)
        self.node_orchestrator = NodeOrchestrationService(db)

    @staticmethod
    def _unique_assignment_enabled() -> bool:
        return PoolAssignmentService._required_features_enabled()

    @staticmethod
    def _native_hmac(namespace: str, *parts: str) -> str:
        message = "\0".join((namespace, *parts))
        return hmac.new(
            settings.pool_bridge_api_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _build_unique_device_config(self, sub, device, region_code: str) -> dict:
        if settings.device_bound_gate_enabled:
            # This legacy native contract stores only a caller-supplied device
            # fingerprint and has no verified public key. Never issue a gated
            # URI that the gateway cannot bind cryptographically. The signed
            # activation/bridge flow is the supported device-bound path.
            raise HTTPException(status_code=503, detail="native_device_key_binding_required")
        service = PoolAssignmentService(self.db)
        expires_at = sub.ends_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        subject_key = self._native_hmac(
            "native-device-v1",
            str(sub.id),
            str(device.id),
            device.device_fingerprint,
        )
        entitlement_hash = self._native_hmac(
            "native-entitlement-v1",
            str(sub.id),
            sub.plan_code,
            expires_at.isoformat(),
        )
        prepared = service.prepare(
            PoolReservationPrepareRequest(
                subject_type="native_device",
                subject_key=subject_key,
                entitlement_hash=entitlement_hash,
                entitlement_expires_at=expires_at,
                region_code=region_code,
            )
        )
        if region_code != "auto" and prepared.region_code != region_code:
            raise HTTPException(status_code=409, detail="device_assignment_region_locked")
        if prepared.confirmation_required:
            service.confirm(
                PoolReservationConfirmRequest(
                    assignment_id=prepared.assignment_id,
                    confirmation_token=prepared.confirmation_token,
                )
            )
        return {
            "node_id": prepared.node_id,
            "node_name": prepared.node_name,
            "region_code": prepared.region_code,
            "import_text": _sanitize_public_import_text(prepared.config),
            "node_health_status": "healthy",
        }

    def redeem_code(self, req: RedeemActivationCodeRequest) -> RedeemActivationCodeResponse:
        user = self.repo.get_or_create_user(req.telegram_id)
        normalized_code_hash = hash_activation_code(req.code.strip().upper())
        code = self.repo.get_activation_code(normalized_code_hash)
        logger.debug("redeem lookup: telegram_id=%s code_found=%s", req.telegram_id, bool(code))
        if not code or code.user_id != user.id:
            self.audit.write("user", str(user.id), "redeem_invalid_code", "activation_code", "unknown")
            self.db.commit()
            raise HTTPException(status_code=401, detail="invalid_or_expired_code")
        sub = self.repo.get_subscription(code.subscription_id)
        if not sub or sub.status != "active" or self._as_utc_naive(sub.ends_at) <= datetime.utcnow():
            raise HTTPException(status_code=403, detail="subscription_inactive")
        try:
            self._register_device_inner(sub.id, req.device_fingerprint, req.platform, req.device_name)
        except HTTPException as exc:
            if exc.status_code == 409:
                self.audit.write("user", str(user.id), "redeem_device_limit_reached", "subscription", str(sub.id))
                self.db.commit()
            raise
        if code.first_redeemed_at is None:
            code.first_redeemed_at = datetime.now(timezone.utc)
        self.audit.write("user", str(user.id), "redeem_activation_code", "subscription", str(sub.id))
        logger.info("redeem succeeded: telegram_id=%s subscription_id=%s", req.telegram_id, sub.id)
        self.db.commit()
        return RedeemActivationCodeResponse(valid=True, expires_at=sub.ends_at, plan_name=sub.plan_code, subscription_id=sub.id)

    def get_status(self, telegram_id: int) -> SubscriptionStatusResponse:
        user = self.repo.get_or_create_user(telegram_id)
        sub = self.repo.get_active_subscription(user.id)
        if not sub:
            self.db.commit()
            return SubscriptionStatusResponse(active=False, devices_limit=5)
        used = self.repo.count_active_devices(sub.id)
        self.db.commit()
        return SubscriptionStatusResponse(
            active=True,
            subscription_id=sub.id,
            plan_code=sub.plan_code,
            ends_at=sub.ends_at,
            devices_used=used,
            devices_limit=sub.devices_limit,
        )

    def register_device(self, req: RegisterDeviceRequest) -> RegisterDeviceResponse:
        user = self.repo.get_or_create_user(req.telegram_id)
        sub = self.repo.get_active_subscription(user.id)
        if not sub:
            raise HTTPException(status_code=403, detail="subscription_inactive")
        device = self._register_device_inner(sub.id, req.device_fingerprint, req.platform, req.device_name)
        used = self.repo.count_active_devices(sub.id)
        self.audit.write("user", str(user.id), "register_device", "device", str(device.id))
        self.db.commit()
        return RegisterDeviceResponse(device_id=device.id, devices_used=used, devices_limit=sub.devices_limit)

    def _register_device_inner(self, subscription_id: int, fingerprint: str, platform: str, device_name: str):
        sub = self.repo.get_subscription(subscription_id)
        if not sub:
            raise HTTPException(status_code=404, detail="subscription_not_found")
        existing = self.repo.find_device(subscription_id, fingerprint)
        if not existing:
            used = self.repo.count_active_devices(subscription_id)
            if used >= sub.devices_limit:
                raise HTTPException(status_code=409, detail="device_limit_reached")
        device = self.repo.upsert_device(
            subscription_id,
            fingerprint,
            platform,
            device_name,
            sub.devices_limit,
        )
        if device is None:
            raise HTTPException(status_code=409, detail="device_limit_reached")
        self.db.flush()
        if self._unique_assignment_enabled():
            try:
                self._build_unique_device_config(sub, device, "auto")
            except HTTPException:
                if existing is None:
                    # Pool prepare can commit while installing the remote
                    # credential.  Remove the just-created native device if
                    # assignment ultimately failed, matching legacy rollback.
                    self.db.delete(device)
                    self.db.commit()
                raise
        return device

    def _resolve_device_for_subscription(self, subscription_id: int, device_fingerprint: str | None):
        devices = self.repo.list_devices(subscription_id)
        if not devices:
            raise HTTPException(status_code=409, detail="device_not_registered")
        if not device_fingerprint:
            if len(devices) == 1:
                return devices[0]
            raise HTTPException(status_code=409, detail="device_fingerprint_required")
        device = self.repo.find_active_device(subscription_id, device_fingerprint)
        if not device:
            raise HTTPException(status_code=404, detail="device_not_registered")
        return device

    def heartbeat(self, req: HeartbeatRequest) -> None:
        user = self.repo.get_or_create_user(req.telegram_id)
        sub = self.repo.get_active_subscription(user.id)
        if not sub:
            raise HTTPException(status_code=403, detail="subscription_inactive")
        if not self.repo.heartbeat(sub.id, req.device_fingerprint):
            raise HTTPException(status_code=404, detail="device_not_found")
        self.audit.write("user", str(user.id), "device_heartbeat", "subscription", str(sub.id))
        self.db.commit()

    def unbind(self, telegram_id: int, fingerprint: str) -> None:
        raise HTTPException(status_code=403, detail="device_unbind_disabled")

    def get_vpn_config(self, telegram_id: int, device_fingerprint: str | None = None) -> VpnConfigResponse:
        agent_log(
            hypothesis_id="H2",
            location="subscription_service.py:get_vpn_config",
            message="get_vpn_config_enter",
            data={"telegram_id": telegram_id, "has_device_fingerprint": bool(device_fingerprint)},
        )
        user = self.repo.get_or_create_user(telegram_id)
        sub = self.repo.get_active_subscription(user.id)
        if not sub:
            agent_log(
                hypothesis_id="H2",
                location="subscription_service.py:get_vpn_config",
                message="get_vpn_config_no_active_subscription",
                data={"telegram_id": telegram_id, "user_id": user.id},
            )
            return VpnConfigResponse(error="subscription_inactive")
        try:
            device = self._resolve_device_for_subscription(sub.id, device_fingerprint)
            if self._unique_assignment_enabled():
                cfg = self._build_unique_device_config(sub, device, sub.region_code)
            else:
                cfg = self.node_orchestrator.build_user_config(sub.id, device)
        except HTTPException as exc:
            agent_log(
                hypothesis_id="H2",
                location="subscription_service.py:get_vpn_config",
                message="get_vpn_config_build_failed",
                data={"subscription_id": sub.id, "error": str(exc.detail)},
            )
            return VpnConfigResponse(error=str(exc.detail))
        self.audit.write("user", str(user.id), "vpn_config_requested", "vpn_node", str(cfg["node_id"]))
        self.db.commit()
        public_import_text = _sanitize_public_import_text(cfg["import_text"])
        agent_log(
            hypothesis_id="H2",
            location="subscription_service.py:get_vpn_config",
            message="get_vpn_config_success",
            data={
                "subscription_id": sub.id,
                "node_id": cfg.get("node_id"),
                "import_text_len": len(public_import_text),
            },
        )
        return VpnConfigResponse(import_text=public_import_text)

    def get_vpn_pool_config(self, access_key: str) -> dict:
        code, sub = self.resolve_subscription_by_access_key(access_key)
        if not code or not sub:
            raise HTTPException(status_code=401, detail="invalid_or_expired_key")
        if self._unique_assignment_enabled():
            raise HTTPException(status_code=409, detail="per_device_region_selection_required")
        import_text = self.node_orchestrator.build_pool_import_text(sub.id)
        if not import_text.strip():
            raise HTTPException(status_code=404, detail="no_pool_config")
        self.audit.write("user", str(code.user_id), "vpn_pool_config_requested", "subscription", str(sub.id))
        self.db.commit()
        return {"importText": _sanitize_public_import_text(import_text)}

    def list_user_devices(self, telegram_id: int) -> list[dict]:
        user = self.repo.get_or_create_user(telegram_id)
        sub = self.repo.get_active_subscription(user.id)
        if not sub:
            self.db.commit()
            return []
        devices = self.repo.list_devices(sub.id)
        self.db.commit()
        return [
            {
                "device_fingerprint": d.device_fingerprint,
                "platform": d.platform,
                "device_name": d.device_name,
                "last_seen_at": d.last_seen_at,
            }
            for d in devices
        ]

    def list_user_codes(self, telegram_id: int) -> list[dict]:
        user = self.repo.get_or_create_user(telegram_id)
        codes = self.repo.list_codes_by_user(user.id)
        self.db.commit()
        return [
            {
                "status": c.status,
                "created_at": c.created_at,
                "first_redeemed_at": c.first_redeemed_at,
            }
            for c in codes
        ]

    def resolve_subscription_by_access_key(self, access_key: str):
        normalized = access_key.strip().upper()
        if not normalized:
            return None, None
        code_hash = hash_activation_code(normalized)
        code = self.repo.get_activation_code(code_hash)
        if not code:
            return None, None
        sub = self.repo.get_subscription(code.subscription_id)
        if not sub:
            return None, None
        if sub.status != "active" or self._as_utc_naive(sub.ends_at) <= datetime.utcnow():
            return None, None
        return code, sub

    def list_vpn_servers(self) -> list[dict]:
        rows = self.node_orchestrator.list_region_entries()
        public_rows: list[dict] = []
        for row in rows:
            public_label = _sanitize_public_server_label(
                str(row.get("city") or row.get("region_name") or ""),
                fallback="Server",
            )
            public_rows.append({**row, "city": public_label, "region_name": public_label})
        agent_log(
            hypothesis_id="H1",
            location="subscription_service.py:list_vpn_servers",
            message="vpn regions listed",
            data={"count": len(public_rows)},
        )
        return public_rows

    def connect_to_server(self, access_key: str, server_id: int, device_fingerprint: str | None = None) -> dict:
        code, sub = self.resolve_subscription_by_access_key(access_key)
        if not code or not sub:
            agent_log(
                hypothesis_id="H2",
                location="subscription_service.py:connect_to_server",
                message="access key validation failed",
                data={"server_id": server_id},
            )
            raise HTTPException(status_code=401, detail="invalid_or_expired_key")

        device = self._resolve_device_for_subscription(sub.id, device_fingerprint)
        if self._unique_assignment_enabled():
            requested_node = self.db.get(VpnNode, server_id)
            if requested_node is None:
                raise HTTPException(status_code=404, detail="server_not_found")
            unique = self._build_unique_device_config(sub, device, requested_node.region_code)
            selected_node = self.db.get(VpnNode, unique["node_id"])
            if selected_node is None:
                raise HTTPException(status_code=409, detail="assigned_node_missing")
            cfg = {"node": selected_node, "import_text": unique["import_text"]}
        else:
            cfg = self.node_orchestrator.build_user_config_for_node(sub.id, server_id, device)
        self.audit.write("user", str(code.user_id), "vpn_connect_requested", "vpn_node", str(server_id))
        self.db.commit()
        public_import_text = _sanitize_public_import_text(cfg["import_text"])
        agent_log(
            hypothesis_id="H4",
            location="subscription_service.py:connect_to_server",
            message="vpn connect payload built",
            data={"server_id": server_id, "region_code": cfg["node"].region_code, "import_len": len(public_import_text)},
        )
        return {
            "server_id": cfg["node"].id,
            "city": cfg["node"].region_code,
            "region_code": cfg["node"].region_code,
            "import_text": public_import_text,
        }

    @staticmethod
    def _as_utc_naive(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
