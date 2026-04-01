from __future__ import annotations

import logging
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

    def choose_best_moscow_node(self):
        node = self.repo.best_active_node("moscow")
        logger.debug("best_moscow_node selected: %s", node.id if node else None)
        return node

    def build_user_config(self, subscription_id: int, device: Device | None) -> dict:
        subscription = self.repo.get_subscription(subscription_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="subscription_not_found")
        nodes = [
            n
            for n in self.repo.list_nodes(None)
            if n.status == "active"
            and n.health_status in {"healthy", "degraded"}
            and n.current_clients < n.capacity_clients
        ]
        if not nodes:
            raise HTTPException(status_code=404, detail="no_healthy_node")
        node = sorted(
            nodes,
            key=lambda n: (
                0 if n.health_status == "healthy" else 1,
                n.load_score,
                -n.priority,
                n.id,
            ),
        )[0]
        if device:
            self.repo.assign_device_to_node(device, node)
        links: list[str] = []
        seen: set[str] = set()
        for candidate in nodes:
            if not candidate.config_payload.strip() and candidate.provider == "firstvds" and hasattr(self.provisioning, "bootstrap_existing_node"):
                logger.info("lazy bootstrap for firstvds node %s (no config_payload)", candidate.id)
                bootstrap_result = self.provisioning.bootstrap_existing_node(candidate)
                logger.info(
                    "lazy bootstrap node %s result=%s config_ready=%s",
                    candidate.id, bootstrap_result.get("status"), bool(candidate.config_payload.strip()),
                )
            link = self.config_service.build_import_text(candidate, subscription, device).strip()
            if link and link not in seen:
                seen.add(link)
                links.append(link)
        import_text = "\n".join(links)
        logger.info(
            "built vpn config: sub=%s nodes=%d links=%d primary_node=%s",
            subscription.id, len(nodes), len(links), node.id,
        )
        self.audit.write(
            "system",
            "orchestrator",
            "vpn_config_built",
            "subscription",
            str(subscription.id),
            {"node_id": node.id},
        )
        self.db.commit()
        return {
            "node_id": node.id,
            "node_name": node.name,
            "region_code": node.region_code,
            "import_text": import_text,
            "node_health_status": node.health_status,
        }

    def list_connectable_nodes(self) -> list:
        return [
            n
            for n in self.repo.list_nodes(None)
            if n.status == "active"
            and n.health_status in {"healthy", "degraded"}
            and n.current_clients < n.capacity_clients
        ]

    def build_user_config_for_node(self, subscription_id: int, node_id: int, device: Device | None) -> dict:
        subscription = self.repo.get_subscription(subscription_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="subscription_not_found")
        node = self.repo.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="server_not_found")
        if (
            node.status != "active"
            or node.health_status not in {"healthy", "degraded"}
            or node.current_clients >= node.capacity_clients
        ):
            # #region agent log
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
            # #endregion
            raise HTTPException(status_code=409, detail="server_unavailable")

        if device:
            self.repo.assign_device_to_node(device, node)
        import_text = self.config_service.build_import_text(node, subscription, device).strip()
        if not import_text:
            # #region agent log
            agent_log(
                hypothesis_id="H5",
                location="node_orchestration_service.py:build_user_config_for_node",
                message="server config unavailable",
                data={"node_id": node_id},
            )
            # #endregion
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
