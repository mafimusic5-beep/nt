"""Plan paid VPN resource ownership without touching live assignments.

This module is intentionally authority-side.  It answers which registered
installation may own one of the paid 1/2/5 Xray resources and, when capacity is
full, which existing assignment is safe to recycle.  A resource is never
selected for recycling while that installation has an unexpired live lease.

Actual pool/Xray mutation is performed by the bridge layer in a later phase;
keeping selection here makes the safety rule testable independently of network
or Xray failures.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceOwner:
    device_row_id: int
    device_id: str
    assignment_id: int
    last_seen_at: str
    online: bool


def _owners(con: sqlite3.Connection, code: str, now: float) -> list[ResourceOwner]:
    rows = con.execute(
        '''
        SELECT d.id AS device_row_id,
               d.device_id,
               d.pool_assignment_id,
               COALESCE(d.last_seen_at, d.activated_at, '') AS last_seen_at,
               CASE WHEN EXISTS (
                   SELECT 1
                   FROM vpn_live_leases l
                   WHERE l.device_row_id = d.id AND l.expires_at > ?
               ) THEN 1 ELSE 0 END AS online
        FROM code_devices d
        WHERE d.code = ?
          AND d.active = 1
          AND d.pool_assignment_id IS NOT NULL
          AND d.pool_status = 'active'
        ORDER BY online DESC, last_seen_at DESC, d.id ASC
        ''',
        (now, code),
    ).fetchall()
    return [
        ResourceOwner(
            device_row_id=int(row['device_row_id']),
            device_id=str(row['device_id']),
            assignment_id=int(row['pool_assignment_id']),
            last_seen_at=str(row['last_seen_at'] or ''),
            online=bool(row['online']),
        )
        for row in rows
    ]


def choose_recyclable_assignment(
    con: sqlite3.Connection,
    *,
    code: str,
    requesting_device_row_id: int,
    limit: int,
    now: float | None = None,
) -> ResourceOwner | None:
    """Return an offline assignment that may be recycled, or ``None``.

    ``None`` has two meanings that callers can distinguish by inspecting their
    own current assignment: either paid capacity is not full (allocate a new
    resource) or every paid resource is currently protected by a live lease
    (fail closed and wait).  The function itself never mutates storage.
    """
    if limit not in (1, 2, 5):
        raise ValueError('unsupported_concurrent_limit')
    now = time.time() if now is None else float(now)
    owners = _owners(con, code, now)

    # The requesting installation already owns a resource.  Reuse it; never
    # rotate credentials merely because the profile was refreshed.
    if any(owner.device_row_id == requesting_device_row_id for owner in owners):
        return None

    # Capacity is not full yet.  The bridge may allocate a fresh resource.
    if len(owners) < limit:
        return None

    # Never steal from an online installation.  Prefer the least recently seen
    # offline owner so resource churn remains deterministic and bounded.
    offline = [owner for owner in owners if not owner.online]
    if not offline:
        return None
    return min(offline, key=lambda owner: (owner.last_seen_at, owner.device_row_id))


def resource_state(
    con: sqlite3.Connection,
    *,
    code: str,
    requesting_device_row_id: int,
    limit: int,
    now: float | None = None,
) -> str:
    """Return ``owned``, ``free``, ``recyclable`` or ``busy``."""
    if limit not in (1, 2, 5):
        raise ValueError('unsupported_concurrent_limit')
    now = time.time() if now is None else float(now)
    owners = _owners(con, code, now)
    if any(owner.device_row_id == requesting_device_row_id for owner in owners):
        return 'owned'
    if len(owners) < limit:
        return 'free'
    if any(not owner.online for owner in owners):
        return 'recyclable'
    return 'busy'
