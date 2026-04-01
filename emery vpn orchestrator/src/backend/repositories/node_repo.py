from __future__ import annotations

from sqlalchemy import case, select

from src.backend.repositories.base import BaseRepository
from src.common.models import Device, Subscription, VpnNode


class NodeRepository(BaseRepository):
    def list_nodes(self, region_code: str | None = None) -> list[VpnNode]:
        stmt = select(VpnNode)
        if region_code:
            stmt = stmt.where(VpnNode.region_code == region_code)
        return self.db.scalars(stmt.order_by(VpnNode.id.asc())).all()

    def get_node(self, node_id: int) -> VpnNode | None:
        return self.db.get(VpnNode, node_id)

    def best_active_node(self, region_code: str = "moscow") -> VpnNode | None:
        health_rank = case(
            (VpnNode.health_status == "healthy", 0),
            (VpnNode.health_status == "degraded", 1),
            else_=2,
        )
        return self.db.scalar(
            select(VpnNode)
            .where(
                VpnNode.region_code == region_code,
                VpnNode.status == "active",
                VpnNode.health_status.in_(["healthy", "degraded"]),
                VpnNode.current_clients < VpnNode.capacity_clients,
            )
            .order_by(health_rank.asc(), VpnNode.load_score.asc(), VpnNode.priority.desc(), VpnNode.id.asc())
        )

    def assign_device_to_node(self, device: Device, node: VpnNode) -> None:
        previous_node_id = device.node_id
        if previous_node_id == node.id:
            return
        if previous_node_id:
            prev = self.get_node(previous_node_id)
            if prev and prev.current_clients > 0:
                prev.current_clients -= 1
        device.node_id = node.id
        node.current_clients += 1

    def subscription_devices(self, subscription_id: int) -> list[Device]:
        return self.db.scalars(
            select(Device).where(Device.subscription_id == subscription_id, Device.is_active.is_(True))
        ).all()

    def get_subscription(self, subscription_id: int) -> Subscription | None:
        return self.db.get(Subscription, subscription_id)

    def update_node_metrics(
        self,
        node_id: int,
        *,
        health_status: str | None = None,
        load_score: int | None = None,
        current_clients: int | None = None,
    ) -> VpnNode | None:
        node = self.get_node(node_id)
        if not node:
            return None
        if health_status is not None:
            node.health_status = health_status
        if load_score is not None:
            node.load_score = load_score
        if current_clients is not None:
            node.current_clients = current_clients
        return node
