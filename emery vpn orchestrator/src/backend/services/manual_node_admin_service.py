from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.schemas.admin import (
    ManualNodeBootstrapRequest,
    ManualNodeBootstrapResponse,
    VpnNodeUpsertRequest,
)
from src.backend.services.admin_service import AdminService
from src.backend.services.manual_node_bootstrap_service import ManualNodeBootstrapService
from src.common.config import settings
from src.common.models import VpnNode


class ManualNodeAdminService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit = AuditRepository(db)
        self.admin = AdminService(db)

    def _existing_node_for_endpoint(self, endpoint: str) -> VpnNode | None:
        matches = self.db.scalars(
            select(VpnNode)
            .where(VpnNode.endpoint == endpoint)
            .order_by(VpnNode.id.asc())
        ).all()
        if len(matches) > 1:
            # Do not silently choose between historical duplicate rows. A
            # duplicate endpoint needs one explicit cleanup before bootstrap can
            # safely preserve assignments on the canonical node.
            raise HTTPException(status_code=409, detail="duplicate_node_endpoint")
        return matches[0] if matches else None

    def bootstrap(self, req: ManualNodeBootstrapRequest) -> ManualNodeBootstrapResponse:
        endpoint = req.endpoint.strip()
        node = self._existing_node_for_endpoint(endpoint)

        if node is None:
            draft = self.admin.create_node(
                VpnNodeUpsertRequest(
                    name=req.name.strip() or "Server",
                    region_code=req.region_code.strip().lower(),
                    endpoint=endpoint,
                    config_payload="",
                    device_gate_host=req.device_gate_host,
                    device_gate_port=req.device_gate_port,
                    device_gate_server_name=req.device_gate_server_name,
                    device_gate_spki_sha256=req.device_gate_spki_sha256,
                    provider="manual",
                    status="provisioning",
                    health_status="unknown",
                    load_score=100,
                    priority=0,
                    capacity_clients=req.capacity_clients,
                    bandwidth_limit_mbps=req.bandwidth_limit_mbps,
                    current_clients=0,
                    per_device_speed_limit_mbps=req.per_device_speed_limit_mbps,
                )
            )
            node = self.db.get(VpnNode, draft.id)
            if node is None:
                raise HTTPException(status_code=500, detail="bootstrap_node_create_failed")
        else:
            # Reinstalling a VPS commonly keeps the same public IP. Reuse the
            # existing database row so assignments keep their node identity and
            # /setup_server cannot create a second pool entry for the same IP.
            gate_host, gate_port, gate_server_name, gate_spki = self.admin._validated_gate_fields(
                host=req.device_gate_host,
                port=req.device_gate_port,
                server_name=req.device_gate_server_name,
                spki_sha256=req.device_gate_spki_sha256,
                required=settings.device_bound_gate_enabled,
            )
            node.name = req.name.strip() or "Server"
            node.region_code = req.region_code.strip().lower()
            node.endpoint = endpoint
            node.provider = "manual"
            node.status = "provisioning"
            node.health_status = "unknown"
            node.load_score = 100
            node.priority = 0
            node.capacity_clients = req.capacity_clients
            node.bandwidth_limit_mbps = req.bandwidth_limit_mbps
            node.per_device_speed_limit_mbps = req.per_device_speed_limit_mbps
            node.config_payload = ""
            node.device_gate_host = gate_host
            node.device_gate_port = gate_port
            node.device_gate_server_name = gate_server_name
            node.device_gate_spki_sha256 = gate_spki
            # A provider reimage changes the SSH host key. Keep the orchestrator
            # keypair itself, but require it to be installed and pin the new host
            # key during this bootstrap.
            node.ssh_key_status = "missing"
            node.ssh_host_key = ""
            self.audit.write(
                "admin",
                "api",
                "manual_node_rebootstrap_started",
                "vpn_node",
                str(node.id),
                {"endpoint": endpoint},
            )
            self.db.flush()

        result = ManualNodeBootstrapService().bootstrap_with_password(
            node,
            ssh_user=req.ssh_user,
            ssh_password=req.ssh_password.get_secret_value(),
        )
        if result.get("status") != "ok":
            node.status = "provision_failed"
            node.health_status = "down"
            safe_detail = str(result.get("detail") or "manual_bootstrap_failed")[:120]
            self.audit.write(
                "admin",
                "api",
                "manual_node_bootstrap_failed",
                "vpn_node",
                str(node.id),
                {"detail": safe_detail},
            )
            self.db.commit()
            raise HTTPException(status_code=502, detail=safe_detail)

        node.status = "active"
        node.health_status = "healthy"
        node.provider = "manual"
        isp_egress_enabled = bool(result.get("isp_egress_enabled"))
        self.audit.write(
            "admin",
            "api",
            "manual_node_bootstrapped",
            "vpn_node",
            str(node.id),
            {"policy_ready": True, "isp_egress_enabled": isp_egress_enabled},
        )
        self.db.commit()
        return ManualNodeBootstrapResponse(
            node=self.admin._node_response(node),
            policy_ready=True,
            isp_egress_enabled=isp_egress_enabled,
        )
