from sqlalchemy import select

from src.backend.repositories.base import BaseRepository
from src.common.models import ActivationCode, AuditLog, Device, Order, Payment, Subscription, User, VpnNode


class AdminRepository(BaseRepository):
    def list_nodes(self) -> list[VpnNode]:
        return self.db.scalars(select(VpnNode).order_by(VpnNode.id.asc())).all()

    def get_node(self, node_id: int) -> VpnNode | None:
        return self.db.get(VpnNode, node_id)

    def create_node(
        self,
        name: str,
        region_code: str,
        endpoint: str,
        config_payload: str,
        status: str,
        health_status: str,
        load_score: int,
        priority: int,
        capacity_clients: int,
        bandwidth_limit_mbps: int,
        current_clients: int,
        per_device_speed_limit_mbps: int,
        firstvds_vps_id: str,
        ssh_key_fingerprint: str,
        ssh_key_status: str,
    ) -> VpnNode:
        node = VpnNode(
            name=name,
            region_code=region_code,
            endpoint=endpoint,
            config_payload=config_payload,
            status=status,
            health_status=health_status,
            load_score=load_score,
            priority=priority,
            capacity_clients=capacity_clients,
            bandwidth_limit_mbps=bandwidth_limit_mbps,
            current_clients=current_clients,
            per_device_speed_limit_mbps=per_device_speed_limit_mbps,
            firstvds_vps_id=firstvds_vps_id,
            ssh_key_fingerprint=ssh_key_fingerprint,
            ssh_key_status=ssh_key_status,
        )
        self.db.add(node)
        self.db.flush()
        return node

    def stats(self) -> dict[str, int]:
        return {
            "users": len(self.db.scalars(select(User.id)).all()),
            "subscriptions": len(self.db.scalars(select(Subscription.id)).all()),
            "active_devices": len(self.db.scalars(select(Device.id).where(Device.is_active.is_(True))).all()),
            "orders": len(self.db.scalars(select(Order.id)).all()),
            "payments": len(self.db.scalars(select(Payment.id)).all()),
            "codes": len(self.db.scalars(select(ActivationCode.id)).all()),
        }

    def list_problem_activations(self, limit: int = 50) -> list[AuditLog]:
        return self.db.scalars(
            select(AuditLog)
            .where(AuditLog.action.in_(["redeem_invalid_code", "redeem_device_limit_reached"]))
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        ).all()
