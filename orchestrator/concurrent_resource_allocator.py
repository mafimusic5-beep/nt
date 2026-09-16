"""Plan and transfer paid VPN resources without evicting live sessions.

Registrations stay durable, while only 1/2/5 installation rows may own Xray
resources for a subscription at once.  All decisions are made inside the same
SQLite write transaction as the caller, so a gateway lease acquisition cannot
race a resource transfer.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


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


_POOL_SELECT = '''
    pool_assignment_id,
    pool_status,
    pool_confirmation_token,
    pool_node_id,
    pool_node_name,
    pool_region,
    pool_config,
    pool_config_revision,
    pool_speed_limit_mbps,
    pool_client_port,
    pool_gate_host,
    pool_gate_port,
    pool_gate_server_name,
    pool_gate_spki_sha256,
    pool_entitlement_hash,
    pool_entitlement_expires_at,
    pool_updated_at
'''


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
    return [
        ResourceOwner(
            int(r['device_row_id']),
            str(r['device_id']),
            int(r['pool_assignment_id']),
            str(r['last_seen_at'] or ''),
            bool(r['online']),
        )
        for r in rows
    ]


def plan_resource(
    con: sqlite3.Connection,
    *,
    code: str,
    requesting_device_row_id: int,
    limit: int,
    now: float | None = None,
) -> ResourcePlan:
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


def release_candidate(
    con: sqlite3.Connection,
    *,
    code: str,
    limit: int,
    now: float | None = None,
) -> ResourceOwner | None:
    if limit not in (1, 2, 5):
        raise ValueError('unsupported_concurrent_limit')
    now_epoch = time.time() if now is None else float(now)
    owners = _owners(con, code, now_epoch)
    if len(owners) <= limit:
        return None
    online_count = sum(1 for owner in owners if owner.online)
    offline = [owner for owner in owners if not owner.online]
    keep_offline = max(limit - online_count, 0)
    protected = {
        owner.device_row_id
        for owner in sorted(
            offline,
            key=lambda owner: (owner.last_seen_at, owner.device_row_id),
            reverse=True,
        )[:keep_offline]
    }
    releasable = [owner for owner in offline if owner.device_row_id not in protected]
    if not releasable:
        return None
    return min(releasable, key=lambda owner: (owner.last_seen_at, owner.device_row_id))


def clear_assignment(
    con: sqlite3.Connection,
    *,
    device_row_id: int,
    assignment_id: int,
    now: float | None = None,
) -> bool:
    now_epoch = time.time() if now is None else float(now)
    live = con.execute(
        'SELECT 1 FROM vpn_live_leases WHERE device_row_id = ? AND expires_at > ? LIMIT 1',
        (int(device_row_id), now_epoch),
    ).fetchone()
    if live:
        return False
    cursor = con.execute(
        '''
        UPDATE code_devices
        SET pool_assignment_id = NULL,
            pool_status = '', pool_confirmation_token = '', pool_node_id = NULL,
            pool_node_name = '', pool_region = '', pool_config = '',
            pool_config_revision = 0, pool_speed_limit_mbps = 0,
            pool_client_port = NULL, pool_gate_host = '', pool_gate_port = NULL,
            pool_gate_server_name = '', pool_gate_spki_sha256 = '',
            pool_entitlement_hash = '', pool_entitlement_expires_at = '',
            pool_updated_at = ?
        WHERE id = ? AND active = 1 AND pool_assignment_id = ?
        ''',
        (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            int(device_row_id), int(assignment_id),
        ),
    )
    return cursor.rowcount == 1


def choose_recyclable_assignment(
    con: sqlite3.Connection,
    *,
    code: str,
    requesting_device_row_id: int,
    limit: int,
    now: float | None = None,
) -> ResourceOwner | None:
    return plan_resource(
        con,
        code=code,
        requesting_device_row_id=requesting_device_row_id,
        limit=limit,
        now=now,
    ).recyclable


def resource_state(
    con: sqlite3.Connection,
    *,
    code: str,
    requesting_device_row_id: int,
    limit: int,
    now: float | None = None,
) -> str:
    action = plan_resource(
        con,
        code=code,
        requesting_device_row_id=requesting_device_row_id,
        limit=limit,
        now=now,
    ).action
    return {'allocate': 'free', 'recycle': 'recyclable'}.get(action, action)


def assignment_for_device(
    con: sqlite3.Connection,
    *,
    device_row_id: int,
) -> dict[str, Any] | None:
    row = con.execute(
        f'''SELECT {_POOL_SELECT} FROM code_devices WHERE id = ? AND active = 1''',
        (int(device_row_id),),
    ).fetchone()
    if not row or not row['pool_assignment_id'] or not str(row['pool_config'] or '').strip():
        return None
    return dict(row)


def transfer_assignment(
    con: sqlite3.Connection,
    *,
    from_device_row_id: int,
    to_device_row_id: int,
    now: float | None = None,
) -> dict[str, Any]:
    """Move one existing assignment between device rows inside caller transaction.

    The source must have no unexpired live lease.  Clearing the source happens
    before writing the target so the unique assignment index is never violated.
    """
    if from_device_row_id == to_device_row_id:
        current = assignment_for_device(con, device_row_id=to_device_row_id)
        if current is None:
            raise RuntimeError('resource_assignment_missing')
        return current

    now_epoch = time.time() if now is None else float(now)
    live = con.execute(
        '''SELECT 1 FROM vpn_live_leases
           WHERE device_row_id = ? AND expires_at > ? LIMIT 1''',
        (int(from_device_row_id), now_epoch),
    ).fetchone()
    if live:
        raise RuntimeError('resource_owner_online')

    target = assignment_for_device(con, device_row_id=to_device_row_id)
    if target is not None:
        raise RuntimeError('resource_target_already_owned')

    source = con.execute(
        f'''SELECT {_POOL_SELECT} FROM code_devices WHERE id = ? AND active = 1''',
        (int(from_device_row_id),),
    ).fetchone()
    if not source or not source['pool_assignment_id'] or not str(source['pool_config'] or '').strip():
        raise RuntimeError('resource_assignment_missing')
    assignment = dict(source)

    con.execute(
        '''
        UPDATE code_devices
        SET pool_assignment_id = NULL,
            pool_status = '',
            pool_confirmation_token = '',
            pool_node_id = NULL,
            pool_node_name = '',
            pool_region = '',
            pool_config = '',
            pool_config_revision = 0,
            pool_speed_limit_mbps = 0,
            pool_client_port = NULL,
            pool_gate_host = '',
            pool_gate_port = NULL,
            pool_gate_server_name = '',
            pool_gate_spki_sha256 = '',
            pool_entitlement_hash = '',
            pool_entitlement_expires_at = '',
            pool_updated_at = ?
        WHERE id = ?
        ''',
        (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), int(from_device_row_id)),
    )

    updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cursor = con.execute(
        '''
        UPDATE code_devices
        SET pool_assignment_id = ?,
            pool_status = ?,
            pool_confirmation_token = ?,
            pool_node_id = ?,
            pool_node_name = ?,
            pool_region = ?,
            pool_config = ?,
            pool_config_revision = ?,
            pool_speed_limit_mbps = ?,
            pool_client_port = ?,
            pool_gate_host = ?,
            pool_gate_port = ?,
            pool_gate_server_name = ?,
            pool_gate_spki_sha256 = ?,
            pool_entitlement_hash = ?,
            pool_entitlement_expires_at = ?,
            pool_updated_at = ?
        WHERE id = ? AND active = 1
        ''',
        (
            assignment['pool_assignment_id'],
            assignment['pool_status'],
            assignment['pool_confirmation_token'],
            assignment['pool_node_id'],
            assignment['pool_node_name'],
            assignment['pool_region'],
            assignment['pool_config'],
            assignment['pool_config_revision'],
            assignment['pool_speed_limit_mbps'],
            assignment['pool_client_port'],
            assignment['pool_gate_host'],
            assignment['pool_gate_port'],
            assignment['pool_gate_server_name'],
            assignment['pool_gate_spki_sha256'],
            assignment['pool_entitlement_hash'],
            assignment['pool_entitlement_expires_at'],
            updated_at,
            int(to_device_row_id),
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError('resource_target_missing')
    assignment['pool_updated_at'] = updated_at
    return assignment
