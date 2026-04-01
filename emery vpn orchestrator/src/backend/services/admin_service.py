import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.backend.repositories.admin_repo import AdminRepository
from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.order_repo import OrderRepository
from src.backend.repositories.subscription_repo import SubscriptionRepository
from src.backend.services.node_adapters import FirstVdsBillManagerProvisioningService
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.utils.security import generate_activation_code, hash_activation_code
from src.backend.schemas.admin import GrantSubscriptionRequest, GrantSubscriptionResponse, VpnNodeResponse, VpnNodeUpsertRequest
from src.common.config import settings

logger = logging.getLogger(__name__)


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
        sub = self.order_repo.create_or_extend_subscription(user.id, req.months, settings.max_devices_per_subscription, req.region_code)
        self.audit_repo.write("admin", "api", "grant_subscription", "subscription", str(sub.id), {"months": req.months})
        self.db.commit()
        return GrantSubscriptionResponse(subscription_id=sub.id, ends_at=sub.ends_at)

    @staticmethod
    def _node_response(n) -> VpnNodeResponse:
        return VpnNodeResponse(
            id=n.id,
            name=n.name,
            region_code=n.region_code,
            endpoint=n.endpoint,
            status=n.status,
            health_status=n.health_status,
            load_score=n.load_score,
            priority=n.priority,
            capacity_clients=n.capacity_clients,
            current_clients=n.current_clients,
            bandwidth_limit_mbps=n.bandwidth_limit_mbps,
            per_device_speed_limit_mbps=n.per_device_speed_limit_mbps,
            ssh_key_fingerprint=n.ssh_key_fingerprint,
            ssh_key_status=n.ssh_key_status,
            has_valid_config=FirstVdsBillManagerProvisioningService.is_config_payload_valid(n.config_payload or ""),
        )

    def create_node(self, req: VpnNodeUpsertRequest) -> VpnNodeResponse:
        if req.region_code != "moscow":
            raise HTTPException(status_code=400, detail="only_moscow_region_supported")
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
        )
        self.audit_repo.write("admin", "api", "create_node", "vpn_node", str(node.id), {"region": node.region_code})
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
        self.audit_repo.write("admin", "api", "manual_activation_code_generated", "subscription", str(sub.id))
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

    def run_healthcheck(self) -> dict:
        return self.node_orchestrator.run_healthcheck()
