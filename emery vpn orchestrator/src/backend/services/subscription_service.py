import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.subscription_repo import SubscriptionRepository
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.utils.debug_log import agent_log
from src.backend.utils.node_city import normalize_node_city
from src.backend.schemas.subscription import (
    HeartbeatRequest,
    RedeemActivationCodeRequest,
    RedeemActivationCodeResponse,
    RegisterDeviceRequest,
    RegisterDeviceResponse,
    SubscriptionStatusResponse,
    VpnConfigResponse,
)
from src.backend.utils.security import hash_activation_code

logger = logging.getLogger(__name__)


class SubscriptionService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = SubscriptionRepository(db)
        self.audit = AuditRepository(db)
        self.node_orchestrator = NodeOrchestrationService(db)

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
        return self.repo.upsert_device(subscription_id, fingerprint, platform, device_name)

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
        user = self.repo.get_or_create_user(telegram_id)
        sub = self.repo.get_active_subscription(user.id)
        if not sub:
            raise HTTPException(status_code=403, detail="subscription_inactive")
        if not self.repo.unbind_device(sub.id, fingerprint):
            raise HTTPException(status_code=404, detail="device_not_found")
        self.audit.write("user", str(user.id), "device_unbind", "subscription", str(sub.id))
        self.db.commit()

    def get_vpn_config(self, telegram_id: int) -> VpnConfigResponse:
        # #region agent log
        agent_log(
            hypothesis_id="H2",
            location="subscription_service.py:get_vpn_config",
            message="get_vpn_config_enter",
            data={"telegram_id": telegram_id},
        )
        # #endregion
        user = self.repo.get_or_create_user(telegram_id)
        sub = self.repo.get_active_subscription(user.id)
        if not sub:
            # #region agent log
            agent_log(
                hypothesis_id="H2",
                location="subscription_service.py:get_vpn_config",
                message="get_vpn_config_no_active_subscription",
                data={"telegram_id": telegram_id, "user_id": user.id},
            )
            # #endregion
            return VpnConfigResponse(error="subscription_inactive")
        devices = self.repo.list_devices(sub.id)
        device = devices[0] if devices else None
        # #region agent log
        agent_log(
            hypothesis_id="H2",
            location="subscription_service.py:get_vpn_config",
            message="get_vpn_config_device_selection",
            data={
                "subscription_id": sub.id,
                "device_count": len(devices),
                "device_fp_prefix": (device.device_fingerprint[:10] if device else ""),
            },
        )
        # #endregion
        try:
            cfg = self.node_orchestrator.build_user_config(sub.id, device)
        except HTTPException as exc:
            # #region agent log
            agent_log(
                hypothesis_id="H2",
                location="subscription_service.py:get_vpn_config",
                message="get_vpn_config_build_failed",
                data={"subscription_id": sub.id, "error": str(exc.detail)},
            )
            # #endregion
            return VpnConfigResponse(error=exc.detail)
        self.audit.write("user", str(user.id), "vpn_config_requested", "vpn_node", str(cfg["node_id"]))
        self.db.commit()
        # #region agent log
        agent_log(
            hypothesis_id="H2",
            location="subscription_service.py:get_vpn_config",
            message="get_vpn_config_success",
            data={
                "subscription_id": sub.id,
                "node_id": cfg.get("node_id"),
                "import_text_len": len(cfg.get("import_text", "")),
            },
        )
        # #endregion
        return VpnConfigResponse(import_text=cfg["import_text"])

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
        nodes = self.node_orchestrator.list_connectable_nodes()
        rows: list[dict] = []
        for node in nodes:
            rows.append(
                {
                    "id": node.id,
                    "city": normalize_node_city(node),
                    "health_status": node.health_status,
                    "is_available": True,
                }
            )
        # #region agent log
        agent_log(
            hypothesis_id="H1",
            location="subscription_service.py:list_vpn_servers",
            message="vpn servers listed",
            data={"count": len(rows)},
        )
        # #endregion
        return rows

    def connect_to_server(self, access_key: str, server_id: int) -> dict:
        code, sub = self.resolve_subscription_by_access_key(access_key)
        if not code or not sub:
            # #region agent log
            agent_log(
                hypothesis_id="H2",
                location="subscription_service.py:connect_to_server",
                message="access key validation failed",
                data={"server_id": server_id},
            )
            # #endregion
            raise HTTPException(status_code=401, detail="invalid_or_expired_key")

        devices = self.repo.list_devices(sub.id)
        device = devices[0] if devices else None
        cfg = self.node_orchestrator.build_user_config_for_node(sub.id, server_id, device)
        city = normalize_node_city(cfg["node"])
        self.audit.write("user", str(code.user_id), "vpn_connect_requested", "vpn_node", str(server_id))
        self.db.commit()
        # #region agent log
        agent_log(
            hypothesis_id="H4",
            location="subscription_service.py:connect_to_server",
            message="vpn connect payload built",
            data={"server_id": server_id, "city": city, "import_len": len(cfg["import_text"])},
        )
        # #endregion
        return {"server_id": server_id, "city": city, "import_text": cfg["import_text"]}

    @staticmethod
    def _as_utc_naive(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
