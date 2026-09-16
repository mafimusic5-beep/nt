"""Plan paid VPN resource ownership without touching live assignments.

The authority keeps registrations durable, but only 1/2/5 installations may
own Xray resources for a subscription at once.  This module makes the decision
explicit and never selects a resource protected by an unexpired live lease.
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


@dataclass(frozen=True)
class ResourcePlan:
    action: str
    recyclable: ResourceOwner | None = None


def _owners(con: sqlite3.Connection, code: str, now: float) -> list[ResourceOwner]:
    rows = con.execute(
        '''
        SELECT d.id AS device_row_id,
               d.device_id,
               d.pool_assignment_id,
               COALESCE(d.last_seen_at, d.activated_at, '') AS last_seen_at,
               CASE WHEN EXISTS (
                   SELECT 1 FROM vpn_live_leases l
                   WHERE l.device_row_id = d.id AND l.expires_at > ?
               ) THEN 1 ELSE 0 END AS online
        FROM code_devices d
        WHERE d.code = ? AND d.active = 1
          AND d.pool_assignment_id IS NOT NULL
          AND d.pool_status IN ('active', 'pending')
        ORDER BY online DESC, last_seen_at DESC, d.id ASC
        ''',
        (now, code),
    ).fetchall()
    return [ResourceOwner(int(r['device_row_id']), str(r['device_id']),
                          int(r['pool_assignment_id']), str(r['last_seen_at'] or ''),
                          bool(r['online'])) for r in rows]


def plan_resource(con: sqlite3.Connection, *, code: str,
                  requesting_device_row_id: int, limit: int,
                  now: float | None = None) -> ResourcePlan:
    if limit not in (1, 2, 5):
        raise ValueError('unsupported_concurrent_limit')
    now = time.time() if now is None else float(now)
    owners = _owners(con, code, now)
    if any(o.device_row_id == requesting_device_row_id for o in owners):
        return ResourcePlan('owned')
    if len(owners) < limit:
        return ResourcePlan('allocate')
    offline = [o for o in owners if not o.online]
    if not offline:
        return ResourcePlan('busy')
    victim = min(offline, key=lambda o: (o.last_seen_at, o.device_row_id))
    return ResourcePlan('recycle', victim)


def choose_recyclable_assignment(con: sqlite3.Connection, *, code: str,
                                  requesting_device_row_id: int, limit: int,
                                  now: float | None = None) -> ResourceOwner | None:
    return plan_resource(con, code=code, requesting_device_row_id=requesting_device_row_id,
                         limit=limit, now=now).recyclable


def resource_state(con: sqlite3.Connection, *, code: str,
                   requesting_device_row_id: int, limit: int,
                   now: float | None = None) -> str:
    action = plan_resource(con, code=code, requesting_device_row_id=requesting_device_row_id,
                           limit=limit, now=now).action
    return {'allocate': 'free', 'recycle': 'recyclable'}.get(action, action)
