from __future__ import annotations

from datetime import datetime, timezone

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
from src.backend.services.manual_device_gate_service import ManualDeviceGateService
from src.backend.services.manual_node_bootstrap_service import ManualNodeBootstrapService
from src.backend.services.xray_credential_service import ScriptOrSshXrayCredentialTransport
from src.common.config import settings
from src.common.models import VpnAssignment, VpnNode


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

    def _rebootstrap_gate_fields(
        self,
        req: ManualNodeBootstrapRequest,
        node: VpnNode,
    ) -> tuple[str, int, str, str]:
        """Preserve valid metadata until automatic gate provisioning replaces it.

        /setup_server intentionally only asks for IP/password. Empty gate fields
        therefore never mean "erase the gate". They may reuse a complete stored
        endpoint, or remain empty for a node that will receive its automatic gate
        later in the same bootstrap. Explicit gate values remain strictly
        validated.
        """
        request_has_gate = bool(
            req.device_gate_host.strip()
            or req.device_gate_server_name.strip()
            or req.device_gate_spki_sha256.strip()
            or int(req.device_gate_port) != 24443
        )
        if request_has_gate:
            host = req.device_gate_host
            port = req.device_gate_port
            server_name = req.device_gate_server_name
            spki_sha256 = req.device_gate_spki_sha256
        else:
            host = node.device_gate_host or ""
            port = int(node.device_gate_port or 24443)
            server_name = node.device_gate_server_name or ""
            spki_sha256 = node.device_gate_spki_sha256 or ""

        return self.admin._validated_gate_fields(
            host=host,
            port=port,
            server_name=server_name,
            spki_sha256=spki_sha256,
            required=request_has_gate,
        )

    def _fail_bootstrap(self, node: VpnNode, detail: str) -> None:
        safe_detail = (detail or "manual_bootstrap_failed").replace("\n", " ")[:120]
        node.status = "provision_failed"
        node.health_status = "down"
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

    def _restore_active_assignments(self, node: VpnNode) -> int:
        assignments = self.db.scalars(
            select(VpnAssignment)
            .where(
                VpnAssignment.node_id == node.id,
                VpnAssignment.status == "active",
            )
            .order_by(VpnAssignment.id.asc())
        ).all()
        if not assignments:
            return 0

        transport = ScriptOrSshXrayCredentialTransport()
        for assignment in assignments:
            try:
                result = transport.install(node, assignment)
            except Exception as exc:  # noqa: BLE001
                self._fail_bootstrap(
                    node,
                    f"assignment_restore_failed:{assignment.id}:{type(exc).__name__}",
                )
            safe = (
                result.ok
                and result.rate_limit_enforced
                and result.smtp_block_enforced
                and result.shared_credential_disabled
                and result.direct_ingress_blocked
                and result.device_gate_ready
            )
            if not safe:
                self._fail_bootstrap(
                    node,
                    f"assignment_restore_failed:{assignment.id}:{result.detail}",
                )

        # Only after every remote upsert attests all protections do we expose a
        # new revision to clients. UUIDs, ports and assignment identities stay
        # unchanged, so reimaging a VPS does not consume new device slots.
        now = datetime.now(timezone.utc)
        for assignment in assignments:
            assignment.device_gate_enforced = True
            assignment.installed_at = now
            assignment.config_revision = max(int(assignment.config_revision or 0), 0) + 1
            assignment.prepare_expires_at = None
            assignment.last_error = ""

        self.audit.write(
            "admin",
            "api",
            "manual_node_assignments_restored",
            "vpn_node",
            str(node.id),
            {"count": len(assignments)},
        )
        self.db.flush()
        return len(assignments)

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
            gate_host, gate_port, gate_server_name, gate_spki = self._rebootstrap_gate_fields(
                req,
                node,
            )
            node.name = req.name.strip() or "Server"
            node.region_code = req.region_code.strip().lower()
            node.endpoint = endpoint
            node.provider = "manual"
            node.status = "provisioning"
            node.health_status = "unknown"
            node.load_score = 100
            node.priority = 0
            # Never produce an impossible 15/5 node merely because the admin
            # command's default is lower than assignments already attached to a
            # reimaged server. Existing accounting wins until those users leave.
            node.capacity_clients = max(req.capacity_clients, int(node.current_clients or 0))
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
            self._fail_bootstrap(
                node,
                str(result.get("detail") or "manual_bootstrap_failed"),
            )

        restored_assignments = 0
        if settings.device_bound_gate_enabled:
            gate_result = ManualDeviceGateService().bootstrap(node)
            if gate_result.get("status") != "ok":
                self._fail_bootstrap(
                    node,
                    str(gate_result.get("detail") or "device_gate_bootstrap_failed"),
                )
            node.device_gate_host = str(gate_result["host"])
            node.device_gate_port = int(gate_result["port"])
            node.device_gate_server_name = str(gate_result["server_name"])
            node.device_gate_spki_sha256 = str(gate_result["spki_sha256"])
            self.db.flush()
            restored_assignments = self._restore_active_assignments(node)

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
            {
                "policy_ready": True,
                "isp_egress_enabled": isp_egress_enabled,
                "device_gate_ready": bool(settings.device_bound_gate_enabled),
                "restored_assignments": restored_assignments,
            },
        )
        self.db.commit()
        return ManualNodeBootstrapResponse(
            node=self.admin._node_response(node),
            policy_ready=True,
            isp_egress_enabled=isp_egress_enabled,
        )
