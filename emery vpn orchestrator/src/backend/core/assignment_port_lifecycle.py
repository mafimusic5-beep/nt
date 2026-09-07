from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session

from src.common.models import VpnAssignment


_listener_installed = False


def _retired_client_port(assignment_id: int) -> int:
    """Move revoked rows outside the valid TCP port range while preserving history."""

    return -abs(int(assignment_id))


def _release_revoked_assignment_ports(session: Session, _flush_context, _instances) -> None:
    """Release node/client-port uniqueness whenever an assignment becomes revoked.

    ``vpn_assignments`` keeps revoked rows for audit/idempotency, while the database
    also enforces a unique ``(node_id, client_port)`` pair. Leaving the old positive
    port on a revoked row makes the allocator believe the port is free but causes the
    following INSERT to fail on the unique constraint. A negative per-row sentinel
    preserves the historical row and frees the real TCP port for reuse.
    """

    candidates = set(session.new).union(session.dirty)
    for value in candidates:
        if not isinstance(value, VpnAssignment):
            continue
        if value.id is None or value.status != "revoked":
            continue
        if int(value.client_port or 0) <= 0:
            continue
        value.client_port = _retired_client_port(value.id)


def install_assignment_port_lifecycle() -> None:
    """Register the revoked-port release hook exactly once per process."""

    global _listener_installed
    if _listener_installed:
        return
    event.listen(Session, "before_flush", _release_revoked_assignment_ports)
    _listener_installed = True
