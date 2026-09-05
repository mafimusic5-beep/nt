from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.schemas.admin import (
    ManualNodeBootstrapRequest,
    ManualNodeBootstrapResponse,
    VpnNodeUpsertRequest,
)
from src.backend.services.admin_service import AdminService
from src.backend.services.manual_node_bootstrap_service import ManualNodeBootstrapService
from src.common.models import VpnNode


class ManualNodeAdminService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit = AuditRepository(db)
        self.admin = AdminService(db)

    def bootstrap(self, req: ManualNodeBootstrapRequest) -> ManualNodeBootstrapResponse:
        draft = self.admin.create_node(
            VpnNodeUpsertRequest(
                name=req.name.strip() or "Server",
                region_code=req.region_code.strip().lower(),
                endpoint=req.endpoint.strip(),
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
        self.audit.write(
            "admin",
            "api",
            "manual_node_bootstrapped",
            "vpn_node",
            str(node.id),
            {"policy_ready": True},
        )
        self.db.commit()
        return ManualNodeBootstrapResponse(
            node=self.admin._node_response(node),
            policy_ready=True,
        )
