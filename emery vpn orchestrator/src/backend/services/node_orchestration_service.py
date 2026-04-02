from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.node_repo import NodeRepository
from src.backend.services.node_adapters import (
    FirstVdsBillManagerProvisioningService,
    ShellScriptNodeProvisioningService,
    XrayVlessRealityConfigService,
)
from src.backend.services.node_interfaces import NodeConfigService, NodeProvisioningService
from src.backend.utils.debug_log import agent_log
from src.backend.utils.node_city import normalize_node_city
from src.common.config import settings
from src.common.models import Device

logger = logging.getLogger(__name__)


class NodeOrchestrationService:
    def __init__(
        self,
        db: Session,
        provisioning: NodeProvisioningService | None = None,
        config_service: NodeConfigService | None = None,
    ):
        self.db = db
        self.repo = NodeRepository(db)
        self.audit = AuditRepository(db)
        self.provisioning = provisioning or self._default_provisioning()
        self.config_service = config_service or XrayVlessRealityConfigService()

    @staticmethod
    def _default_provisioning() -> NodeProvisioningService:
        if settings.firstvds_enabled:
            return FirstVdsBillManagerProvisioningService()
        return ShellScriptNodeProvisioningService()

    @staticmethod
    def _is_connectable(node) -> bool:
        return (
            node.status == "active"
            and node.health_status in {"healthy", "degraded"}
            and node.current_clients < node.capacity_clients
        )

    @staticmethod
    def _node_sort_key(node) -> tuple[int, int, int, int]:
        return (
            0 if node.health_status == "healthy" else 1,
            node.load_score,
            -node.priority,
            node.id,
        )

    def _eligible_nodes(self, region_code: str | None = None) -> list:
        return [
            n
            for n in self.repo.list_nodes(region_code)
            if self._is_connectable(n)
        ]

    def choose_best_moscow_node(self):
        node = self.repo.best_active_node("moscow")
        logger.debug("best_moscow_node selected: %s", node.id if node else None)
        return node

    def build_user_config(self, subscription_id: int, device: Device | None) -> dict:
        subscription = self.repo.get_subscription(subscription_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="subscription_not_found")

        nodes = self._eligible_nodes(subscription.region_code)
        if not nodes:
            raise HTTPException(status_code=404, detail="no_healthy_node")

        node = sorted(nodes, key=self._node_sort_key)[0]
        if device:
            self.repo.assign_device_to_node(device, node)

        import_text = self.config_service.build_import_text(node, subscription, device).strip()
        if not import_text:
            raise HTTPException(status_code=409, detail="server_config_unavailable")

        logger.info(
            "built vpn config: sub=%s region=%s node=%s",
            subscription.id,
            subscription.region_code,
            node.id,
        )
        self.audit.write(
            "system",
            "orchestrator",
            "vpn_config_built",
            "subscription",
            str(subscription.id),
            {"node_id": node.id, "region_code": subscription.region_code},
        )
        self.db.commit()
        return {
            "node_id": node.id,
            "node_name": node.name,
            "region_code": node.region_code,
            "import_text": import_text,
            "node_health_status": node.health_status,
        }

    def list_connectable_nodes(self, region_code: str | None = None) -> list:
        return self._eligible_nodes(region_code)

    def list_region_entries(self) -> list[dict]:
        grouped: dict[str, list] = defaultdict(list)
        for node in self._eligible_nodes():
            grouped[node.region_code].append(node)

        rows: list[dict] = []
        for region_code, nodes in grouped.items():
            best = sorted(nodes, key=self._node_sort_key)[0]
            region_name = normalize_node_city(best)
            rows.append(
                {
                    "id": best.id,
                    "city": region_name,
                    "health_status": best.health_status,
                    "is_available": True,
                    "region_code": region_code,
                    "region_name": region_name,
                    "available_nodes": len(nodes),
                }
            )

        return sorted(rows, key=lambda row: (row["region_name"] or "", row["region_code"] or "", row["id"]))

    def build_user_config_for_node(self, subscription_id: int, node_id: int, device: Device | None) -> dict:
        subscription = self.repo.get_subscription(subscription_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="subscription_not_found")
        node = self.repo.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="server_not_found")
        if subscription.region_code and node.region_code != subscription.region_code:
            raise HTTPException(status_code=409, detail="server_region_mismatch")
        if not self._is_connectable(node):
            agent_log(
                hypothesis_id="H3",
                location="node_orchestration_service.py:build_user_config_for_node",
                message="server is not connectable",
                data={
                    "node_id": node_id,
                    "status": node.status,
                    "health_status": node.health_status,
                    "current_clients": node.current_clients,
                    "capacity_clients": node.capacity_clients,
                },
            )
            raise HTTPException(status_code=409, detail="server_unavailable")

        if device:
            self.repo.assign_device_to_node(device, node)
        import_text = self.config_service.build_import_text(node, subscription, device).strip()
        if not import_text:
            agent_log(
                hypothesis_id="H5",
                location="node_orchestration_service.py:build_user_config_for_node",
                message="server config unavailable",
                data={"node_id": node_id},
            )
            raise HTTPException(status_code=409, detail="server_config_unavailable")
        return {"node": node, "import_text": import_text}

    def provision_node(self, node_id: int) -> dict:
        node = self.repo.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="node_not_found")
        logger.info("provision_node invoked for node_id=%s", node.id)
        result = self.provisioning.provision_node(node)
        self.audit.write("admin", "api", "node_provision_requested", "vpn_node", str(node.id), result)
        if result.get("status") == "ok":
            node.status = "active"
            node.health_status = "healthy"
        self.db.commit()
        return {"node_id": node.id, **result}

    def deprovision_node(self, node_id: int) -> dict:
        node = self.repo.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="node_not_found")
        result = self.provisioning.deprovision_node(node)
        self.audit.write("admin", "api", "node_deprovision_requested", "vpn_node", str(node.id), result)
        if result.get("status") == "ok":
            node.status = "maintenance"
            node.health_status = "down"
        self.db.commit()
        return {"node_id": node.id, **result}

    def run_healthcheck(self) -> dict:
        nodes = self.repo.list_nodes(None)
        results = self.provisioning.healthcheck_nodes(nodes)
        for row in results:
            self.repo.update_node_metrics(
                row["node_id"],
                health_status=row.get("health_status"),
                load_score=row.get("load_score"),
            )
        self.audit.write(
            "system",
            "healthcheck",
            "node_healthcheck_completed",
            "vpn_node",
            "all_regions",
            {"count": len(results), "ts": datetime.now(timezone.utc).isoformat()},
        )
        self.db.commit()
        return {"checked": len(results), "results": results}
