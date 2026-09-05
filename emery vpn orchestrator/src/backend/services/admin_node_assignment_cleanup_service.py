from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.services.pool_assignment_service import PoolAssignmentService
from src.backend.services.xray_credential_service import ScriptOrSshXrayCredentialTransport
from src.common.models import VpnAssignment, VpnNode


_XRAY_CLEANUP_SETTLE_SECONDS = 2.5


class AdminNodeAssignmentCleanupService:
    """Explicitly revoke every counted assignment on one VPS and free its slots.

    This is intentionally an admin-only repair operation. It is not part of
    normal maintenance because reimaging a VPS must preserve legitimate device
    assignments. The command is for a node that the operator has confirmed has
    no real clients but still carries stale historical assignments.
    """

    def __init__(self, db: Session, transport=None, sleep_fn=time.sleep) -> None:
        self.db = db
        self.audit = AuditRepository(db)
        self.transport = transport or ScriptOrSshXrayCredentialTransport()
        self.sleep_fn = sleep_fn

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _remaining_count(self, node_id: int) -> int:
        return int(
            self.db.scalar(
                select(func.count(VpnAssignment.id)).where(
                    VpnAssignment.node_id == node_id,
                    VpnAssignment.status.in_(tuple(PoolAssignmentService.COUNTED_STATUSES)),
                )
            )
            or 0
        )

    def clear(self, node_id: int) -> dict[str, object]:
        node = self.db.get(VpnNode, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node_not_found")

        assignments = self.db.scalars(
            select(VpnAssignment)
            .where(
                VpnAssignment.node_id == node.id,
                VpnAssignment.status.in_(tuple(PoolAssignmentService.COUNTED_STATUSES)),
            )
            .order_by(VpnAssignment.id.asc())
        ).all()

        if not assignments:
            # Repair a stale counter even when no counted rows remain.
            node.current_clients = 0
            self.db.commit()
            return {
                "node_id": node.id,
                "cleared": 0,
                "failed": 0,
                "remaining": 0,
                "node_status": node.status,
                "health_status": node.health_status,
            }

        previous_status = node.status
        previous_health = node.health_status
        # Stop the pool from assigning new devices while credentials are being
        # removed and the counter is being reconciled.
        node.status = "maintenance"
        node.health_status = "down"
        self.audit.write(
            "admin",
            "api",
            "admin_node_assignment_cleanup_started",
            "vpn_node",
            str(node.id),
            {"count": len(assignments)},
        )
        self.db.commit()

        cleared = 0
        failed = 0
        last_index = len(assignments) - 1
        for index, assignment in enumerate(assignments):
            # Claim the row first so a concurrent prepare cannot hand the stale
            # credential back to a client while we are removing it remotely.
            assignment.status = "revoking"
            assignment.prepare_expires_at = self._now() + timedelta(minutes=5)
            assignment.last_error = ""
            self.db.commit()

            try:
                result = self.transport.remove(node, assignment)
            except Exception as exc:  # noqa: BLE001
                result = None
                error_detail = f"credential_remove_failed:{type(exc).__name__}"
            else:
                error_detail = "" if result.ok else (result.detail or "credential_remove_failed")

            if result is not None and result.ok:
                assignment.status = "revoked"
                assignment.prepare_expires_at = None
                assignment.confirmation_token_hash = ""
                assignment.device_gate_enforced = False
                assignment.last_error = ""
                cleared += 1
            else:
                assignment.status = "revocation_pending"
                assignment.prepare_expires_at = None
                assignment.last_error = error_detail[:500]
                failed += 1
            self.db.commit()

            # Credential removal currently restarts Xray. Pace bulk cleanup so
            # a node with many stale rows does not trip systemd's start limiter.
            if index < last_index:
                self.sleep_fn(_XRAY_CLEANUP_SETTLE_SECONDS)

        remaining = self._remaining_count(node.id)
        node.current_clients = remaining
        if failed == 0:
            node.status = previous_status
            node.health_status = previous_health
        else:
            # Fail closed if any stale credential could not be removed.
            node.status = "maintenance"
            node.health_status = "down"

        self.audit.write(
            "admin",
            "api",
            "admin_node_assignment_cleanup_completed",
            "vpn_node",
            str(node.id),
            {
                "cleared": cleared,
                "failed": failed,
                "remaining": remaining,
            },
        )
        self.db.commit()
        return {
            "node_id": node.id,
            "cleared": cleared,
            "failed": failed,
            "remaining": remaining,
            "node_status": node.status,
            "health_status": node.health_status,
        }
