from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from fractions import Fraction

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.schemas.pool_bridge import (
    PoolReservationConfirmRequest,
    PoolReservationConfirmResponse,
    PoolReservationPrepareRequest,
    PoolReservationResponse,
)
from src.backend.services.xray_credential_service import (
    CredentialMutationResult,
    ScriptOrSshXrayCredentialTransport,
    VlessDeviceConfigBuilder,
    XrayCredentialTransport,
)
from src.common.config import settings
from src.common.models import VpnAssignment, VpnNode

logger = logging.getLogger(__name__)


class PoolAssignmentService:
    """Reserve exactly one public-pool slot and one VLESS identity per device.

    The reservation is deliberately two-phase.  The pool commits the slot and
    installs the server credential before returning it, while the legacy API
    confirms only after its own SQLite transaction has persisted the response.
    Unconfirmed reservations are removed by ``run_maintenance``.
    """

    COUNTED_STATUSES = {
        "installing",
        "install_claimed",
        "pending",
        "active",
        "revocation_pending",
        "revoking",
    }

    def __init__(
        self,
        db: Session,
        credential_transport: XrayCredentialTransport | None = None,
    ) -> None:
        self.db = db
        self.audit = AuditRepository(db)
        self.transport = credential_transport or ScriptOrSshXrayCredentialTransport()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _required_features_enabled() -> bool:
        return all(
            (
                settings.pool_accounting_bridge_enabled,
                settings.unique_device_credentials_enabled,
                settings.per_device_rate_limit_enforced,
                settings.smtp_abuse_protection_enabled,
                settings.device_bound_gate_enabled,
                bool(settings.pool_bridge_api_key.strip()),
            )
        )

    def _require_enabled(self) -> None:
        if not self._required_features_enabled():
            raise HTTPException(status_code=503, detail="pool_assignment_features_disabled")

    def _find_subject(self, subject_type: str, subject_key: str) -> VpnAssignment | None:
        return self.db.scalar(
            select(VpnAssignment).where(
                VpnAssignment.subject_type == subject_type,
                VpnAssignment.subject_key == subject_key,
            )
        )

    @staticmethod
    def _node_sort_key(node: VpnNode) -> tuple:
        capacity = max(int(node.capacity_clients or 0), 1)
        return (
            Fraction(max(int(node.current_clients or 0), 0), capacity),
            0 if node.health_status == "healthy" else 1,
            int(node.load_score or 0),
            -int(node.priority or 0),
            node.id,
        )

    def _candidate_nodes(self, region_code: str) -> list[VpnNode]:
        stmt = select(VpnNode).where(
            VpnNode.status == "active",
            VpnNode.health_status.in_(("healthy", "degraded")),
            VpnNode.current_clients < VpnNode.capacity_clients,
        )
        if region_code != "auto":
            stmt = stmt.where(VpnNode.region_code == region_code)
        nodes = self.db.scalars(stmt).all()
        if settings.device_bound_gate_enabled:
            nodes = [
                node
                for node in nodes
                if VlessDeviceConfigBuilder.gate_endpoint(node) is not None
            ]
        return sorted(nodes, key=self._node_sort_key)

    def _free_port(self, node_id: int) -> int | None:
        start = max(int(settings.xray_client_port_start), 1024)
        end = min(max(int(settings.xray_client_port_end), start), 65535)
        used = set(
            self.db.scalars(
                select(VpnAssignment.client_port).where(
                    VpnAssignment.node_id == node_id,
                    VpnAssignment.status.in_(tuple(self.COUNTED_STATUSES)),
                )
            ).all()
        )
        return next((port for port in range(start, end + 1) if port not in used), None)

    def _reserve_new(self, req: PoolReservationPrepareRequest) -> VpnAssignment:
        # Updating the counter conditionally takes a per-node write lock and is
        # the actual capacity gate.  The assignment insert and increment commit
        # together, so two workers cannot oversubscribe a node.
        for node in self._candidate_nodes(req.region_code):
            result = self.db.execute(
                update(VpnNode)
                .where(
                    VpnNode.id == node.id,
                    VpnNode.status == "active",
                    VpnNode.health_status.in_(("healthy", "degraded")),
                    VpnNode.current_clients < VpnNode.capacity_clients,
                )
                .values(current_clients=VpnNode.current_clients + 1)
            )
            if result.rowcount != 1:
                self.db.rollback()
                continue
            port = self._free_port(node.id)
            if port is None:
                self.db.rollback()
                continue
            assignment = VpnAssignment(
                subject_type=req.subject_type,
                subject_key=req.subject_key,
                entitlement_hash=req.entitlement_hash,
                entitlement_expires_at=req.entitlement_expires_at,
                node_id=node.id,
                client_uuid=str(uuid.uuid4()),
                client_port=port,
                speed_limit_mbps=max(int(node.per_device_speed_limit_mbps), 1),
                status="installing",
                prepare_expires_at=self._now()
                + timedelta(seconds=max(int(settings.pool_assignment_prepare_ttl_seconds), 30)),
            )
            self.db.add(assignment)
            try:
                self.db.commit()
                self.db.refresh(assignment)
                return assignment
            except IntegrityError:
                self.db.rollback()
                concurrent = self._find_subject(req.subject_type, req.subject_key)
                if concurrent is not None:
                    return concurrent
        raise HTTPException(status_code=409, detail="server_capacity_unavailable")

    def _reactivate_revoked(
        self,
        assignment: VpnAssignment,
        req: PoolReservationPrepareRequest,
    ) -> VpnAssignment:
        for node in self._candidate_nodes(req.region_code):
            result = self.db.execute(
                update(VpnNode)
                .where(
                    VpnNode.id == node.id,
                    VpnNode.status == "active",
                    VpnNode.health_status.in_(("healthy", "degraded")),
                    VpnNode.current_clients < VpnNode.capacity_clients,
                )
                .values(current_clients=VpnNode.current_clients + 1)
            )
            if result.rowcount != 1:
                self.db.rollback()
                continue
            port = self._free_port(node.id)
            if port is None:
                self.db.rollback()
                continue
            assignment.node_id = node.id
            assignment.client_uuid = str(uuid.uuid4())
            assignment.client_port = port
            assignment.speed_limit_mbps = max(int(node.per_device_speed_limit_mbps), 1)
            assignment.entitlement_hash = req.entitlement_hash
            assignment.entitlement_expires_at = req.entitlement_expires_at
            assignment.status = "installing"
            assignment.config_revision += 1
            assignment.confirmation_token_hash = ""
            assignment.confirmed_at = None
            assignment.installed_at = None
            assignment.device_gate_enforced = False
            assignment.last_error = ""
            assignment.prepare_expires_at = self._now() + timedelta(
                seconds=max(int(settings.pool_assignment_prepare_ttl_seconds), 30)
            )
            try:
                self.db.commit()
                self.db.refresh(assignment)
                return assignment
            except IntegrityError:
                self.db.rollback()
                continue
        raise HTTPException(status_code=409, detail="server_capacity_unavailable")

    def _release_failed_install(self, assignment: VpnAssignment, detail: str) -> None:
        node = self.db.get(VpnNode, assignment.node_id)
        if node is not None:
            # Best effort cleanup covers the case where a custom script changed
            # Xray but failed to attest the mandatory safety controls.
            try:
                self.transport.remove(node, assignment)
            except Exception:  # noqa: BLE001
                logger.warning("credential cleanup failed for assignment=%s", assignment.id, exc_info=True)
            self.db.execute(
                update(VpnNode)
                .where(VpnNode.id == node.id, VpnNode.current_clients > 0)
                .values(current_clients=VpnNode.current_clients - 1)
            )
        self.audit.write(
            "system",
            "pool_bridge",
            "vpn_assignment_install_failed",
            "vpn_assignment",
            str(assignment.id),
            {"detail": detail[:500]},
        )
        self.db.delete(assignment)
        self.db.commit()

    def _response(
        self,
        assignment: VpnAssignment,
        *,
        confirmation_token: str = "",
    ) -> PoolReservationResponse:
        node = self.db.get(VpnNode, assignment.node_id)
        if node is None:
            raise HTTPException(status_code=409, detail="assigned_node_missing")
        config = VlessDeviceConfigBuilder.build(node, assignment)
        if not config:
            raise HTTPException(status_code=409, detail="server_config_unavailable")
        return PoolReservationResponse(
            assignment_id=assignment.id,
            status=assignment.status,
            confirmation_required=assignment.status != "active",
            confirmation_token=confirmation_token,
            node_id=node.id,
            node_name=node.name,
            region_code=node.region_code,
            config=config,
            client_port=assignment.client_port,
            device_gate_required=True,
            device_gate_host=node.device_gate_host,
            device_gate_port=node.device_gate_port,
            device_gate_server_name=node.device_gate_server_name,
            device_gate_spki_sha256=node.device_gate_spki_sha256,
            config_revision=assignment.config_revision,
            speed_limit_mbps=assignment.speed_limit_mbps,
            entitlement_expires_at=assignment.entitlement_expires_at,
        )

    def prepare(self, req: PoolReservationPrepareRequest) -> PoolReservationResponse:
        self._require_enabled()
        now = self._now()
        entitlement_expires_at = self._as_utc(req.entitlement_expires_at)
        if entitlement_expires_at <= now:
            raise HTTPException(status_code=403, detail="entitlement_expired")

        assignment = self._find_subject(req.subject_type, req.subject_key)
        if assignment is None:
            assignment = self._reserve_new(req)
        else:
            stored_expiry = self._as_utc(assignment.entitlement_expires_at)
            # A stale retry is allowed to read the current assignment but may
            # never shorten the entitlement recorded by a newer renewal.
            if entitlement_expires_at >= stored_expiry:
                assignment.entitlement_hash = req.entitlement_hash
                assignment.entitlement_expires_at = req.entitlement_expires_at
            if assignment.status == "active" and assignment.device_gate_enforced:
                self.db.commit()
                return self._response(assignment)
            if assignment.status == "active":
                # Rows created before device-gate rollout are untrusted until
                # Xray has been rewritten to loopback and the gateway service
                # has attested readiness. Rotate the visible revision so every
                # client refreshes its local-only URI.
                assignment.config_revision += 1
            if (
                assignment.status == "install_claimed"
                and assignment.prepare_expires_at is not None
                and self._as_utc(assignment.prepare_expires_at) > now
            ):
                raise HTTPException(status_code=409, detail="assignment_install_in_progress")
            if assignment.status == "revoking":
                raise HTTPException(status_code=409, detail="assignment_maintenance_in_progress")
            if assignment.status == "revoked":
                assignment = self._reactivate_revoked(assignment, req)

        node = self.db.get(VpnNode, assignment.node_id)
        if node is None:
            raise HTTPException(status_code=409, detail="assigned_node_missing")
        if not VlessDeviceConfigBuilder.build(node, assignment):
            self._release_failed_install(assignment, "server_config_unavailable")
            raise HTTPException(status_code=409, detail="server_config_unavailable")

        previous_status = assignment.status
        claim_stmt = update(VpnAssignment).where(
            VpnAssignment.id == assignment.id,
            VpnAssignment.status == previous_status,
        )
        if previous_status == "install_claimed":
            claim_stmt = claim_stmt.where(
                or_(
                    VpnAssignment.prepare_expires_at.is_(None),
                    VpnAssignment.prepare_expires_at <= now,
                )
            )
        claimed = self.db.execute(
            claim_stmt
            .values(
                status="install_claimed",
                last_error="",
                prepare_expires_at=now
                + timedelta(seconds=max(int(settings.pool_assignment_prepare_ttl_seconds), 30)),
            )
        )
        self.db.commit()
        if claimed.rowcount != 1:
            raise HTTPException(status_code=409, detail="assignment_state_changed_retry")
        self.db.refresh(assignment)
        result = self.transport.install(node, assignment)
        if (
            not result.ok
            or not result.rate_limit_enforced
            or not result.smtp_block_enforced
            or not result.shared_credential_disabled
            or not result.direct_ingress_blocked
            or not result.device_gate_ready
        ):
            detail = result.detail
            if result.ok and not result.rate_limit_enforced:
                detail = "rate_limit_not_enforced"
            elif result.ok and not result.smtp_block_enforced:
                detail = "smtp_block_not_enforced"
            elif result.ok and not result.shared_credential_disabled:
                detail = "shared_credential_not_disabled"
            elif result.ok and not result.direct_ingress_blocked:
                detail = "direct_ingress_not_blocked"
            elif result.ok and not result.device_gate_ready:
                detail = "device_gate_not_ready"
            self._release_failed_install(assignment, detail)
            raise HTTPException(status_code=503, detail="credential_install_failed")

        confirmation_token = secrets.token_urlsafe(32)
        assignment.status = "pending"
        assignment.installed_at = now
        assignment.device_gate_enforced = True
        assignment.confirmation_token_hash = hashlib.sha256(
            confirmation_token.encode("utf-8")
        ).hexdigest()
        assignment.prepare_expires_at = now + timedelta(
            seconds=max(int(settings.pool_assignment_prepare_ttl_seconds), 30)
        )
        self.audit.write(
            "system",
            "pool_bridge",
            "vpn_assignment_prepared",
            "vpn_assignment",
            str(assignment.id),
            {"node_id": assignment.node_id, "region_code": node.region_code},
        )
        self.db.commit()
        return self._response(assignment, confirmation_token=confirmation_token)

    def confirm(self, req: PoolReservationConfirmRequest) -> PoolReservationConfirmResponse:
        self._require_enabled()
        assignment = self.db.get(VpnAssignment, req.assignment_id)
        if assignment is None:
            raise HTTPException(status_code=404, detail="assignment_not_found")
        supplied = hashlib.sha256(req.confirmation_token.encode("utf-8")).hexdigest()
        if not assignment.confirmation_token_hash or not hmac.compare_digest(
            supplied, assignment.confirmation_token_hash
        ):
            raise HTTPException(status_code=403, detail="confirmation_token_invalid")
        if assignment.status == "active" and assignment.confirmed_at is not None:
            return PoolReservationConfirmResponse(
                assignment_id=assignment.id,
                status=assignment.status,
                confirmed_at=assignment.confirmed_at,
            )
        if assignment.status != "pending":
            raise HTTPException(status_code=409, detail="assignment_not_pending")
        if not assignment.device_gate_enforced:
            raise HTTPException(status_code=409, detail="device_gate_not_enforced")
        if assignment.prepare_expires_at is None or self._as_utc(assignment.prepare_expires_at) <= self._now():
            raise HTTPException(status_code=409, detail="assignment_confirmation_expired")

        assignment.status = "active"
        assignment.confirmed_at = self._now()
        assignment.prepare_expires_at = None
        self.audit.write(
            "system",
            "pool_bridge",
            "vpn_assignment_confirmed",
            "vpn_assignment",
            str(assignment.id),
            {"node_id": assignment.node_id},
        )
        self.db.commit()
        return PoolReservationConfirmResponse(
            assignment_id=assignment.id,
            status=assignment.status,
            confirmed_at=assignment.confirmed_at,
        )

    def _migrate_unprotected_assignments(self) -> tuple[int, int, int]:
        if not self._required_features_enabled():
            return 0, 0, 0
        rows = self.db.scalars(
            select(VpnAssignment).where(
                VpnAssignment.status == "active",
                VpnAssignment.device_gate_enforced.is_(False),
            )
        ).all()
        checked = migrated = failed = 0
        for assignment in rows:
            checked += 1
            claimed = self.db.execute(
                update(VpnAssignment)
                .where(
                    VpnAssignment.id == assignment.id,
                    VpnAssignment.status == "active",
                    VpnAssignment.device_gate_enforced.is_(False),
                )
                .values(
                    status="install_claimed",
                    prepare_expires_at=self._now()
                    + timedelta(
                        seconds=max(
                            int(settings.pool_assignment_prepare_ttl_seconds),
                            60,
                        )
                    ),
                )
            )
            self.db.commit()
            if claimed.rowcount != 1:
                continue
            self.db.refresh(assignment)
            node = self.db.get(VpnNode, assignment.node_id)
            if node is None or not VlessDeviceConfigBuilder.build(node, assignment):
                self._release_failed_install(assignment, "device_gate_migration_node_invalid")
                failed += 1
                continue
            try:
                result = self.transport.install(node, assignment)
            except Exception as exc:  # noqa: BLE001
                result = CredentialMutationResult(
                    False,
                    f"device_gate_migration_failed:{type(exc).__name__}",
                )
            if not (
                result.ok
                and result.rate_limit_enforced
                and result.smtp_block_enforced
                and result.shared_credential_disabled
                and result.direct_ingress_blocked
                and result.device_gate_ready
            ):
                self._release_failed_install(assignment, result.detail)
                failed += 1
                continue
            assignment.status = "active"
            assignment.device_gate_enforced = True
            assignment.config_revision += 1
            assignment.installed_at = self._now()
            assignment.prepare_expires_at = None
            assignment.last_error = ""
            self.audit.write(
                "system",
                "pool_maintenance",
                "vpn_assignment_device_gate_migrated",
                "vpn_assignment",
                str(assignment.id),
                {"node_id": node.id},
            )
            self.db.commit()
            migrated += 1
        return checked, migrated, failed

    def run_maintenance(self) -> dict[str, int]:
        """Revoke expired or abandoned assignments without reassigning users."""

        now = self._now()
        gate_checked, migrated, gate_failed = self._migrate_unprotected_assignments()
        rows = self.db.scalars(
            select(VpnAssignment).where(
                VpnAssignment.status.in_(tuple(self.COUNTED_STATUSES)),
            )
        ).all()
        checked = gate_checked
        revoked = 0
        failed = gate_failed
        for assignment in rows:
            expired_entitlement = self._as_utc(assignment.entitlement_expires_at) <= now
            abandoned_prepare = (
                assignment.status in {"installing", "install_claimed", "pending"}
                and assignment.prepare_expires_at is not None
                and self._as_utc(assignment.prepare_expires_at) <= now
            )
            expired_revoke_lease = (
                assignment.status == "revoking"
                and (
                    assignment.prepare_expires_at is None
                    or self._as_utc(assignment.prepare_expires_at) <= now
                )
            )
            if (
                not expired_entitlement
                and not abandoned_prepare
                and assignment.status != "revocation_pending"
                and not expired_revoke_lease
            ):
                continue
            checked += 1
            previous_status = assignment.status
            revoke_claim_stmt = update(VpnAssignment).where(
                VpnAssignment.id == assignment.id,
                VpnAssignment.status == previous_status,
            )
            if previous_status == "revoking":
                revoke_claim_stmt = revoke_claim_stmt.where(
                    or_(
                        VpnAssignment.prepare_expires_at.is_(None),
                        VpnAssignment.prepare_expires_at <= now,
                    )
                )
            claimed = self.db.execute(
                revoke_claim_stmt
                .values(
                    status="revoking",
                    prepare_expires_at=now
                    + timedelta(seconds=max(int(settings.pool_assignment_prepare_ttl_seconds), 60)),
                )
            )
            self.db.commit()
            if claimed.rowcount != 1:
                continue
            self.db.refresh(assignment)
            node = self.db.get(VpnNode, assignment.node_id)
            if node is None:
                assignment.status = "revocation_pending"
                assignment.last_error = "assigned_node_missing"
                failed += 1
                continue
            result = self.transport.remove(node, assignment)
            if not result.ok:
                assignment.status = "revocation_pending"
                assignment.last_error = result.detail[:500]
                failed += 1
                continue
            assignment.status = "revoked"
            assignment.last_error = ""
            assignment.prepare_expires_at = None
            self.db.execute(
                update(VpnNode)
                .where(VpnNode.id == node.id, VpnNode.current_clients > 0)
                .values(current_clients=VpnNode.current_clients - 1)
            )
            self.audit.write(
                "system",
                "pool_maintenance",
                "vpn_assignment_revoked",
                "vpn_assignment",
                str(assignment.id),
                {"node_id": node.id, "reason": "expired" if expired_entitlement else "unconfirmed"},
            )
            revoked += 1
        self.db.commit()
        return {
            "checked": checked,
            "migrated": migrated,
            "revoked": revoked,
            "failed": failed,
        }
