import logging
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.backend.repositories.admin_repo import AdminRepository
from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.order_repo import OrderRepository
from src.backend.repositories.subscription_repo import SubscriptionRepository
from src.backend.services.node_adapters import FirstVdsBillManagerProvisioningService
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.utils.security import generate_activation_code, hash_activation_code
from src.backend.schemas.admin import (
    GrantSubscriptionRequest,
    GrantSubscriptionResponse,
    VpnNodeDeviceGateRequest,
    VpnNodeResponse,
    VpnNodeUpsertRequest,
)
from src.common.config import settings

logger = logging.getLogger(__name__)
_GATE_NAME_RE = re.compile(r"^[A-Za-z0-9.-]{1,255}$")


class AdminService:
    def __init__(self, db: Session):
        self.db = db
        self.admin_repo = AdminRepository(db)
        self.sub_repo = SubscriptionRepository(db)
        self.order_repo = OrderRepository(db)
        self.audit_repo = AuditRepository(db)
        self.node_orchestrator = NodeOrchestrationService(db)

    def grant_subscription(self, req: GrantSubscriptionRequest) -> GrantSubscriptionResponse:
        if req.months <= 0:
            raise HTTPException(status_code=400, detail="invalid_months")
        user = self.sub_repo.get_or_create_user(req.telegram_id)
        sub = self.order_repo.create_or_extend_subscription(
            user.id,
            req.months,
            settings.max_devices_per_subscription,
            req.region_code,
        )
        self.audit_repo.write(
            "admin",
            "api",
            "grant_subscription",
            "subscription",
            str(sub.id),
            {"months": req.months},
        )
        self.db.commit()
        return GrantSubscriptionResponse(subscription_id=sub.id, ends_at=sub.ends_at)

    @staticmethod
    def _validated_gate_fields(
        *,
        host: str,
        port: int,
        server_name: str,
        spki_sha256: str,
        required: bool,
    ) -> tuple[str, int, str, str]:
        safe_host = host.strip().lower()
        safe_server_name = server_name.strip().lower()
        safe_spki = spki_sha256.strip().lower()
        any_value = bool(safe_host or safe_server_name or safe_spki or int(port) != 24443)
        if not required and not any_value:
            return "", int(port), "", ""
        if not safe_host or not safe_server_name or not safe_spki:
            raise HTTPException(status_code=400, detail="device_gate_endpoint_required")
        if any(
            not _GATE_NAME_RE.fullmatch(value)
            or value.startswith(".")
            or value.endswith(".")
            for value in (safe_host, safe_server_name)
        ):
            raise HTTPException(status_code=400, detail="device_gate_endpoint_invalid")
        if port < 1 or port > 65535:
            raise HTTPException(status_code=400, detail="device_gate_port_invalid")
        if not re.fullmatch(r"[a-f0-9]{64}", safe_spki):
            raise HTTPException(status_code=400, detail="device_gate_spki_invalid")
        return safe_host, int(port), safe_server_name, safe_spki

    @staticmethod
    def _node_response(n) -> VpnNodeResponse:
        return VpnNodeResponse(
            id=n.id,
            name=n.name,
            region_code=n.region_code,
            provider=n.provider,
            endpoint=n.endpoint,
            device_gate_host=n.device_gate_host,
            device_gate_port=n.device_gate_port,
            device_gate_server_name=n.device_gate_server_name,
            device_gate_spki_sha256=n.device_gate_spki_sha256,
            status=n.status,
            health_status=n.health_status,
            load_score=n.load_score,
            priority=n.priority,
            capacity_clients=n.capacity_clients,
            current_clients=n.current_clients,
            bandwidth_limit_mbps=n.bandwidth_limit_mbps,
            per_device_speed_limit_mbps=n.per_device_speed_limit_mbps,
            provider_server_id=n.provider_server_id,
            contract_id=n.contract_id,
            paid_until=n.paid_until,
            renewal_price_eur_cents=n.renewal_price_eur_cents,
            auto_renew=n.auto_renew,
            renewal_status=n.renewal_status,
            do_not_renew_reason=n.do_not_renew_reason,
            ssh_key_fingerprint=n.ssh_key_fingerprint,
            ssh_key_status=n.ssh_key_status,
            ssh_host_key_pinned=bool(n.ssh_host_key),
            has_valid_config=FirstVdsBillManagerProvisioningService.is_config_payload_valid(
                n.config_payload or ""
            ),
            consecutive_health_failures=n.consecutive_health_failures,
            recovery_status=n.recovery_status,
            recovery_lock_until=n.recovery_lock_until,
            last_healthy_at=n.last_healthy_at,
            last_recovery_at=n.last_recovery_at,
            last_recovery_action=n.last_recovery_action,
            last_recovery_error=n.last_recovery_error,
        )

    def create_node(self, req: VpnNodeUpsertRequest) -> VpnNodeResponse:
        if not req.region_code.strip():
            raise HTTPException(status_code=400, detail="region_required")
        if not req.endpoint.strip():
            raise HTTPException(status_code=400, detail="endpoint_required")
        if req.config_payload.strip() and not FirstVdsBillManagerProvisioningService.is_config_payload_valid(
            req.config_payload
        ):
            raise HTTPException(status_code=400, detail="invalid_vless_config")
        gate_host, gate_port, gate_server_name, gate_spki = self._validated_gate_fields(
            host=req.device_gate_host,
            port=req.device_gate_port,
            server_name=req.device_gate_server_name,
            spki_sha256=req.device_gate_spki_sha256,
            required=settings.device_bound_gate_enabled,
        )
        node = self.admin_repo.create_node(
            req.name,
            req.region_code,
            req.endpoint,
            req.config_payload,
            req.status,
            req.health_status,
            req.load_score,
            req.priority,
            req.capacity_clients,
            req.bandwidth_limit_mbps,
            req.current_clients,
            req.per_device_speed_limit_mbps,
            req.firstvds_vps_id,
            req.ssh_key_fingerprint,
            req.ssh_key_status,
            req.provider,
            req.ssh_host_key,
            req.provider_server_id,
            req.contract_id,
            req.paid_until,
            req.renewal_price_eur_cents,
            req.auto_renew,
            req.renewal_status,
            req.do_not_renew_reason,
            gate_host,
            gate_port,
            gate_server_name,
            gate_spki,
        )
        self.audit_repo.write(
            "admin",
            "api",
            "create_node",
            "vpn_node",
            str(node.id),
            {"region": node.region_code, "provider": node.provider},
        )
        self.db.commit()
        return self._node_response(node)

    def configure_node_device_gate(
        self,
        node_id: int,
        req: VpnNodeDeviceGateRequest,
    ) -> VpnNodeResponse:
        node = self.admin_repo.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="node_not_found")
        host, port, server_name, spki = self._validated_gate_fields(
            host=req.device_gate_host,
            port=req.device_gate_port,
            server_name=req.device_gate_server_name,
            spki_sha256=req.device_gate_spki_sha256,
            required=True,
        )
        node.device_gate_host = host
        node.device_gate_port = port
        node.device_gate_server_name = server_name
        node.device_gate_spki_sha256 = spki
        self.audit_repo.write(
            "admin",
            "api",
            "configure_node_device_gate",
            "vpn_node",
            str(node.id),
            {"host": host, "port": port, "server_name": server_name},
        )
        self.db.commit()
        return self._node_response(node)

    def list_nodes(self) -> list[VpnNodeResponse]:
        nodes = self.admin_repo.list_nodes()
        return [self._node_response(n) for n in nodes]

    def stats(self) -> dict[str, int]:
        return self.admin_repo.stats()

    def generate_code(self, telegram_id: int) -> dict:
        user = self.sub_repo.get_or_create_user(telegram_id)
        sub = self.sub_repo.get_active_subscription(user.id)
        if not sub:
            raise HTTPException(status_code=404, detail="active_subscription_not_found")
        plain = generate_activation_code(12)
        self.order_repo.create_activation_code(user.id, sub.id, hash_activation_code(plain))
        self.audit_repo.write(
            "admin",
            "api",
            "manual_activation_code_generated",
            "subscription",
            str(sub.id),
        )
        self.db.commit()
        return {"activation_code": plain, "subscription_id": sub.id}

    def problem_activations(self) -> list[dict]:
        rows = self.admin_repo.list_problem_activations()
        return [
            {
                "created_at": r.created_at,
                "actor_id": r.actor_id,
                "action": r.action,
                "entity_id": r.entity_id,
                "details": r.details,
            }
            for r in rows
        ]

    def best_moscow_node(self) -> dict:
        node = self.node_orchestrator.choose_best_moscow_node()
        if not node:
            raise HTTPException(status_code=404, detail="no_suitable_moscow_node")
        return {
            "id": node.id,
            "name": node.name,
            "region_code": node.region_code,
            "status": node.status,
            "health_status": node.health_status,
            "load_score": node.load_score,
            "priority": node.priority,
            "capacity_clients": node.capacity_clients,
            "current_clients": node.current_clients,
        }

    def provision_node(self, node_id: int) -> dict:
        return self.node_orchestrator.provision_node(node_id)

    def deprovision_node(self, node_id: int) -> dict:
        return self.node_orchestrator.deprovision_node(node_id)

    def set_node_enabled(self, node_id: int, enabled: bool) -> dict:
        node = self.admin_repo.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="node_not_found")
        node.status = "active" if enabled else "maintenance"
        node.health_status = "healthy" if enabled else "down"
        if enabled:
            node.provisioning_lock_key = None
        action = "enable_node" if enabled else "disable_node"
        self.audit_repo.write("admin", "api", action, "vpn_node", str(node.id))
        self.db.commit()
        return {
            "node_id": node.id,
            "status": "ok",
            "detail": "enabled" if enabled else "disabled",
        }

    def run_healthcheck(self) -> dict:
        return self.node_orchestrator.run_healthcheck()
