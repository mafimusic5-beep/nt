from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.services.pool_assignment_service import PoolAssignmentService
from src.backend.services.xray_credential_service import ScriptOrSshXrayCredentialTransport
from src.common.models import VpnAssignment, VpnNode


_XRAY_CLEANUP_SETTLE_SECONDS = 2.5
_CLEANUP_LEASE_MINUTES = 10
_REMOVE_ATTEMPTS = 3
_REMOVE_RETRY_SECONDS = 5.0
_REVOCATION_STATUSES = {"revocation_pending", "revoking"}


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

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

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

    def _remove_with_retry(self, node: VpnNode, assignment: VpnAssignment):
        result = None
        error_detail = "credential_remove_failed"
        for attempt in range(1, _REMOVE_ATTEMPTS + 1):
            try:
                result = self.transport.remove(node, assignment)
            except Exception as exc:  # noqa: BLE001
                result = None
                error_detail = f"credential_remove_failed:{type(exc).__name__}"
            else:
                if result.ok:
                    return result, ""
                error_detail = result.detail or "credential_remove_failed"

            if attempt < _REMOVE_ATTEMPTS:
                self.sleep_fn(_REMOVE_RETRY_SECONDS)
        return result, error_detail

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

        now = self._now()
        # A second /clear_slots must never run against the same credentials while
        # the first cleanup is still removing them remotely. The lease also lets
        # an interrupted cleanup be retried safely after it expires.
        for assignment in assignments:
            expires_at = assignment.prepare_expires_at
            if (
                assignment.status == "revoking"
                and expires_at is not None
                and self._as_utc(expires_at) > now
            ):
                raise HTTPException(status_code=409, detail="node_assignment_cleanup_in_progress")

        # If the only counted rows left are revocation rows and the node was left
        # maintenance/down by an earlier failed cleanup, a successful retry must
        # return it to service. This is exactly the state produced by fail-closed
        # cleanup; intentional maintenance with ordinary active rows is preserved.
        resume_failed_cleanup = (
            node.status == "maintenance"
            and node.health_status == "down"
            and all(assignment.status in _REVOCATION_STATUSES for assignment in assignments)
        )
        previous_status = node.status
        previous_health = node.health_status
        restore_active = (
            node.status == "active" and node.health_status in {"healthy", "degraded"}
        ) or resume_failed_cleanup

        assignment_ids = [assignment.id for assignment in assignments]
        lease_expires_at = now + timedelta(minutes=_CLEANUP_LEASE_MINUTES)

        # Claim the whole cleanup set atomically before the first SSH mutation.
        # If another worker claimed any row after our SELECT, roll back and fail
        # fast instead of letting two cleanup loops restart Xray concurrently.
        claimed = self.db.execute(
            update(VpnAssignment)
            .where(
                VpnAssignment.id.in_(assignment_ids),
                VpnAssignment.status.in_(tuple(PoolAssignmentService.COUNTED_STATUSES)),
                or_(
                    VpnAssignment.status != "revoking",
                    VpnAssignment.prepare_expires_at.is_(None),
                    VpnAssignment.prepare_expires_at <= now,
                ),
            )
            .values(
                status="revoking",
                prepare_expires_at=lease_expires_at,
                last_error="",
            )
            .execution_options(synchronize_session=False)
        ).rowcount or 0
        if claimed != len(assignment_ids):
            self.db.rollback()
            raise HTTPException(status_code=409, detail="node_assignment_cleanup_in_progress")

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
            {"count": len(assignment_ids)},
        )
        self.db.commit()

        assignments = self.db.scalars(
            select(VpnAssignment)
            .where(VpnAssignment.id.in_(assignment_ids))
            .order_by(VpnAssignment.id.asc())
        ).all()

        cleared = 0
        failed = 0
        last_index = len(assignments) - 1
        for index, assignment in enumerate(assignments):
            result, error_detail = self._remove_with_retry(node, assignment)

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
        if failed == 0 and remaining == 0:
            if restore_active:
                node.status = "active"
                node.health_status = "healthy"
            else:
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
