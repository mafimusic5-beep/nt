from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.services.admin_node_assignment_cleanup_service import (
    AdminNodeAssignmentCleanupService,
)
from src.backend.services.pool_assignment_service import PoolAssignmentService
from src.common.models import Device, VpnAssignment, VpnNode


class AdminNodeDeleteService:
    """Remove a VPS from the pool and database without leaving stale references.

    `/delconfig` is an explicit destructive admin action. If the node still has
    counted device assignments, revoke their remote credentials first. Any
    credential-removal failure keeps the node in maintenance/down and aborts the
    database deletion so access accounting can never claim the node disappeared
    while a credential may still exist on the VPS.
    """

    def __init__(self, db: Session, cleanup_service=None) -> None:
        self.db = db
        self.audit = AuditRepository(db)
        self.cleanup = cleanup_service or AdminNodeAssignmentCleanupService(db)

    def delete(self, node_id: int) -> dict[str, object]:
        node = self.db.get(VpnNode, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node_not_found")

        counted_assignment_id = self.db.scalar(
            select(VpnAssignment.id)
            .where(
                VpnAssignment.node_id == node.id,
                VpnAssignment.status.in_(tuple(PoolAssignmentService.COUNTED_STATUSES)),
            )
            .limit(1)
        )
        if counted_assignment_id is not None:
            cleanup = self.cleanup.clear(node.id)
            if int(cleanup.get("failed") or 0) or int(cleanup.get("remaining") or 0):
                raise HTTPException(status_code=409, detail="node_assignment_cleanup_failed")
            node = self.db.get(VpnNode, node_id)
            if node is None:
                raise HTTPException(status_code=404, detail="node_not_found")

        # The node must never be selectable again while local references are
        # being detached and its historical assignment rows are removed.
        node.status = "maintenance"
        node.health_status = "down"
        self.db.commit()

        self.db.execute(
            update(Device)
            .where(Device.node_id == node.id)
            .values(node_id=None)
        )
        removed_assignments = self.db.execute(
            delete(VpnAssignment).where(VpnAssignment.node_id == node.id)
        ).rowcount or 0

        self.audit.write(
            "admin",
            "api",
            "delete_node",
            "vpn_node",
            str(node.id),
            {
                "endpoint": node.endpoint,
                "removed_assignments": int(removed_assignments),
            },
        )
        deleted_id = node.id
        self.db.delete(node)
        self.db.commit()
        return {
            "node_id": deleted_id,
            "status": "ok",
            "detail": "deleted",
            "removed_assignments": int(removed_assignments),
        }
