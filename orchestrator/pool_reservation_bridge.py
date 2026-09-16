from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

import concurrent_sessions
from concurrent_resource_allocator import (
    assignment_for_device, claim_assignment_release, clear_assignment, plan_resource,
    release_candidate, restore_assignment_release, transfer_assignment,
)

from config import (
    DATABASE_PATH,
    POOL_BRIDGE_API_KEY,
    POOL_BRIDGE_ENABLED,
    POOL_BRIDGE_PSEUDONYM_KEY,
    POOL_BRIDGE_REGION_CODE,
    POOL_BRIDGE_TIMEOUT_SECONDS,
    POOL_BRIDGE_URL,
)
from device_identity_aliases import get_pool_subject_alias


_REGION_RE = re.compile(r'^[a-z0-9-]{1,64}$')
_MAX_POOL_SPEED_LIMIT_MBPS = 50
_VALID_TRAFFIC_POLICIES = {'international', 'russia'}


@dataclass
class PoolBridgeError(RuntimeError):
    reason: str
    status_code: int = 503

    def __str__(self) -> str:
        return self.reason


def is_enabled() -> bool:
    return bool(
        POOL_BRIDGE_ENABLED
        and POOL_BRIDGE_URL
        and POOL_BRIDGE_API_KEY
        and POOL_BRIDGE_PSEUDONYM_KEY
    )


def _pseudonym(namespace: str, *parts: str) -> str:
    message = '\0'.join((namespace, *(str(part).strip() for part in parts)))
    return hmac.new(
        POOL_BRIDGE_PSEUDONYM_KEY.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _subject_key(formatted_code: str, device_id: str) -> str:
    preserved = get_pool_subject_alias(formatted_code, device_id)
    if preserved:
        return preserved
    return _pseudonym('legacy-device-v1', formatted_code, device_id)


def _entitlement_hash(formatted_code: str, plan: str, expires_at: str) -> str:
    return _pseudonym('legacy-entitlement-v1', formatted_code, plan, expires_at)


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f'pool_http_{response.status_code}'
    if isinstance(payload, dict):
        return str(payload.get('detail') or payload.get('error') or f'pool_http_{response.status_code}')
    return f'pool_http_{response.status_code}'


def _request(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not is_enabled():
        raise PoolBridgeError('pool_bridge_not_configured', 503)
    try:
        response = httpx.post(
            POOL_BRIDGE_URL + path,
            headers={'X-Pool-Bridge-Key': POOL_BRIDGE_API_KEY},
            json=payload,
            timeout=POOL_BRIDGE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise PoolBridgeError('pool_backend_unreachable', 503) from exc
    if response.status_code >= 400:
        detail = _error_detail(response)
        status = 409 if response.status_code == 409 else 403 if response.status_code == 403 else 503
        raise PoolBridgeError(detail, status)
    try:
        result = response.json()
    except ValueError as exc:
        raise PoolBridgeError('invalid_pool_backend_response', 503) from exc
    if not isinstance(result, dict):
        raise PoolBridgeError('invalid_pool_backend_response', 503)
    return result


def _validated_assignment(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        assignment_id = int(payload['assignment_id'])
        node_id = int(payload['node_id'])
        revision = int(payload['config_revision'])
        speed = int(payload['speed_limit_mbps'])
        config = str(payload['config']).strip()
        parsed = urlsplit(config)
        parsed_uuid = uuid.UUID(parsed.username or '')
        local_port = parsed.port
        client_port = int(payload['client_port'])
        gate_port = int(payload['device_gate_port'])
    except (KeyError, TypeError, ValueError) as exc:
        raise PoolBridgeError('invalid_pool_assignment', 503) from exc
    if assignment_id <= 0 or node_id <= 0 or revision <= 0:
        raise PoolBridgeError('invalid_pool_assignment', 503)
    if (
        parsed.scheme != 'vless'
        or parsed.hostname != '127.0.0.1'
        or local_port is None
        or not (1024 <= local_port <= 65535)
    ):
        raise PoolBridgeError('invalid_pool_assignment_config', 503)
    if str(parsed_uuid) != (parsed.username or '').lower():
        raise PoolBridgeError('invalid_pool_assignment_uuid', 503)
    if speed <= 0 or speed > _MAX_POOL_SPEED_LIMIT_MBPS:
        raise PoolBridgeError('invalid_pool_speed_limit', 503)
    if not bool(payload.get('device_gate_required')):
        raise PoolBridgeError('device_gate_not_required', 503)
    gate_host = str(payload.get('device_gate_host') or '').strip()
    gate_server_name = str(payload.get('device_gate_server_name') or '').strip()
    gate_spki_sha256 = str(payload.get('device_gate_spki_sha256') or '').strip().lower()
    if not gate_host or not gate_server_name:
        raise PoolBridgeError('device_gate_endpoint_missing', 503)
    if not re.fullmatch(r'[a-f0-9]{64}', gate_spki_sha256):
        raise PoolBridgeError('device_gate_spki_invalid', 503)
    if not (1024 <= client_port <= 65535) or not (1 <= gate_port <= 65535):
        raise PoolBridgeError('device_gate_port_invalid', 503)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(query_pairs)
    gate_fields = (
        'eg_v',
        'eg_host',
        'eg_port',
        'eg_sni',
        'eg_spki',
        'eg_assignment',
        'eg_node',
    )
    if any(sum(1 for key, _ in query_pairs if key == field) != 1 for field in gate_fields):
        raise PoolBridgeError('device_gate_metadata_invalid', 503)
    if (
        query.get('eg_v') != '1'
        or query.get('eg_host') != gate_host
        or query.get('eg_port') != str(gate_port)
        or query.get('eg_sni') != gate_server_name
        or query.get('eg_spki') != gate_spki_sha256
        or query.get('eg_assignment') != str(assignment_id)
        or query.get('eg_node') != str(node_id)
    ):
        raise PoolBridgeError('device_gate_metadata_missing', 503)

    region = str(payload.get('region_code') or '').strip().lower()
    if not _REGION_RE.fullmatch(region):
        raise PoolBridgeError('invalid_pool_region', 503)
    confirmation_required = bool(payload.get('confirmation_required'))
    token = str(payload.get('confirmation_token') or '').strip()
    if confirmation_required and len(token) < 32:
        raise PoolBridgeError('missing_pool_confirmation_token', 503)
    return {
        'pool_assignment_id': assignment_id,
        'pool_status': str(payload.get('status') or 'pending')[:32],
        'pool_confirmation_token': token,
        'pool_node_id': node_id,
        'pool_node_name': str(payload.get('node_name') or f'Server {node_id}')[:128],
        'pool_region': region,
        'pool_config': config,
        'pool_config_revision': revision,
        'pool_speed_limit_mbps': speed,
        'pool_client_port': client_port,
        'pool_gate_host': gate_host,
        'pool_gate_port': gate_port,
        'pool_gate_server_name': gate_server_name,
        'pool_gate_spki_sha256': gate_spki_sha256,
        'pool_entitlement_expires_at': str(payload.get('entitlement_expires_at') or ''),
        'confirmation_required': confirmation_required,
    }


def prepare_assignment(
    *,
    formatted_code: str,
    device_id: str,
    plan: str,
    expires_at: str,
) -> dict[str, Any]:
    region = POOL_BRIDGE_REGION_CODE if _REGION_RE.fullmatch(POOL_BRIDGE_REGION_CODE) else 'auto'
    entitlement = _entitlement_hash(formatted_code, plan, expires_at)
    response = _request(
        '/api/v1/internal/pool/assignments/prepare',
        {
            'subject_type': 'legacy_device',
            'subject_key': _subject_key(formatted_code, device_id),
            'entitlement_hash': entitlement,
            'entitlement_expires_at': expires_at,
            'region_code': region,
        },
    )
    result = _validated_assignment(response)
    result['pool_entitlement_hash'] = entitlement
    return result


def rebind_assignment(
    *,
    assignment_id: int,
    formatted_code: str,
    device_id: str,
    plan: str,
    expires_at: str,
) -> dict[str, Any]:
    """Move pool ownership metadata without creating a new Xray resource."""
    if assignment_id <= 0:
        raise PoolBridgeError('invalid_pool_assignment', 409)
    entitlement = _entitlement_hash(formatted_code, plan, expires_at)
    response = _request(
        '/api/v1/internal/pool/assignments/rebind',
        {
            'assignment_id': assignment_id,
            'subject_type': 'legacy_device',
            'subject_key': _subject_key(formatted_code, device_id),
            'entitlement_hash': entitlement,
            'entitlement_expires_at': expires_at,
        },
    )
    result = _validated_assignment(response)
    if int(result.get('pool_assignment_id') or 0) != assignment_id:
        raise PoolBridgeError('pool_assignment_identity_changed', 503)
    result['pool_entitlement_hash'] = entitlement
    return result


def release_assignment(*, assignment_id: int, formatted_code: str, device_id: str) -> dict[str, Any]:
    if assignment_id <= 0:
        raise PoolBridgeError('invalid_pool_assignment', 409)
    response = _request(
        '/api/v1/internal/pool/assignments/release',
        {
            'assignment_id': assignment_id,
            'subject_type': 'legacy_device',
            'subject_key': _subject_key(formatted_code, device_id),
        },
    )
    if (
        response.get('ok') is not True
        or int(response.get('assignment_id') or 0) != assignment_id
        or str(response.get('status') or '') != 'revoked'
    ):
        raise PoolBridgeError('pool_release_failed', 503)
    return response


def reconcile_concurrent_resources() -> int:
    if not concurrent_sessions.enabled() or not is_enabled():
        return 0
    released = 0
    while True:
        con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys = ON')
        device_row_id = 0
        assignment_id = 0
        device_id = ''
        code = ''
        previous_status = ''
        try:
            con.execute('BEGIN IMMEDIATE')
            concurrent_sessions.ensure_storage(con)

            pending = con.execute(
                '''
                SELECT id, code, device_id, pool_assignment_id, pool_status
                FROM code_devices
                WHERE active = 1
                  AND pool_assignment_id IS NOT NULL
                  AND pool_status IN ('release_pending_active', 'release_pending_pending')
                ORDER BY id ASC
                LIMIT 1
                '''
            ).fetchone()
            if pending is not None:
                device_row_id = int(pending['id'])
                assignment_id = int(pending['pool_assignment_id'])
                device_id = str(pending['device_id'])
                code = str(pending['code'])
                previous_status = str(pending['pool_status']).removeprefix('release_pending_')
            else:
                rows = con.execute(
                    '''
                    SELECT code, max_devices
                    FROM activation_codes
                    WHERE status = 'active' AND max_devices IN (1, 2, 5)
                    ORDER BY id ASC
                    '''
                ).fetchall()
                for row in rows:
                    maybe = release_candidate(
                        con, code=str(row['code']), limit=int(row['max_devices'])
                    )
                    if maybe is None:
                        continue
                    claimed_status = claim_assignment_release(
                        con,
                        device_row_id=maybe.device_row_id,
                        assignment_id=maybe.assignment_id,
                    )
                    if claimed_status is None:
                        continue
                    device_row_id = maybe.device_row_id
                    assignment_id = maybe.assignment_id
                    device_id = maybe.device_id
                    code = str(row['code'])
                    previous_status = claimed_status
                    break
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

        if assignment_id <= 0:
            return released

        try:
            release_assignment(
                assignment_id=assignment_id,
                formatted_code=code,
                device_id=device_id,
            )
        except PoolBridgeError as error:
            if error.reason == 'assignment_owner_changed':
                con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
                con.row_factory = sqlite3.Row
                con.execute('PRAGMA foreign_keys = ON')
                try:
                    con.execute('BEGIN IMMEDIATE')
                    restore_assignment_release(
                        con,
                        device_row_id=device_row_id,
                        assignment_id=assignment_id,
                        previous_status=previous_status,
                    )
                    con.commit()
                except Exception:
                    con.rollback()
                    raise
                finally:
                    con.close()
            return released

        con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys = ON')
        try:
            con.execute('BEGIN IMMEDIATE')
            concurrent_sessions.ensure_storage(con)
            if not clear_assignment(
                con,
                device_row_id=device_row_id,
                assignment_id=assignment_id,
            ):
                raise RuntimeError('released_pool_assignment_not_clearable')
            released += 1
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def confirm_assignment(assignment_id: int, confirmation_token: str) -> dict[str, Any]:
    if assignment_id <= 0 or len(confirmation_token.strip()) < 32:
        raise PoolBridgeError('invalid_pool_confirmation', 503)
    return _request(
        '/api/v1/internal/pool/assignments/confirm',
        {
            'assignment_id': assignment_id,
            'confirmation_token': confirmation_token,
        },
    )


def confirm_persisted_assignment(assignment: dict[str, Any]) -> dict[str, Any]:
    """Confirm after the local transaction; leave retry data on failure."""

    from storage import mark_device_pool_assignment_confirmed

    token = str(assignment.get('pool_confirmation_token') or '')
    assignment_id = int(assignment.get('pool_assignment_id') or 0)
    if not bool(assignment.get('confirmation_required')):
        mark_device_pool_assignment_confirmed(assignment_id)
        assignment['pool_status'] = 'active'
        assignment['pool_confirmation_token'] = ''
        return assignment
    confirmed = confirm_assignment(assignment_id, token)
    if str(confirmed.get('status') or '') != 'active':
        raise PoolBridgeError('pool_confirmation_failed', 503)
    mark_device_pool_assignment_confirmed(assignment_id)
    assignment['pool_status'] = 'active'
    assignment['pool_confirmation_token'] = ''
    assignment['confirmation_required'] = False
    return assignment


def _concurrent_entitlement_row(con: sqlite3.Connection, raw_code: str, device_id: str):
    from storage import format_code

    return con.execute(
        """
        SELECT c.code, c.plan, c.expires_at, c.max_devices, d.id AS device_row_id,
               d.pool_status AS pool_status
        FROM activation_codes c
        JOIN code_devices d ON d.code = c.code
        WHERE c.code = ?
          AND c.status = 'active'
          AND d.device_id = ?
          AND d.active = 1
        """,
        (format_code(raw_code), device_id.strip()[:128]),
    ).fetchone()


def _refresh_concurrent_assignment(raw_code: str, device_id: str) -> dict[str, Any]:
    from storage import save_device_pool_assignment, save_device_pool_assignment_in_connection

    con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    assignment: dict[str, Any] | None = None
    code = ''
    plan = ''
    expires_at = ''
    safe_device_id = device_id.strip()[:128]
    try:
        con.execute('BEGIN IMMEDIATE')
        concurrent_sessions.ensure_storage(con)
        row = _concurrent_entitlement_row(con, raw_code, safe_device_id)
        if row is None:
            raise PoolBridgeError('device_not_registered', 403)
        if str(row['pool_status'] or '').startswith('release_pending_'):
            raise PoolBridgeError('pool_assignment_maintenance_in_progress', 409)
        code = str(row['code'])
        plan = str(row['plan'] or '')
        expires_at = str(row['expires_at'] or '').strip()
        if not expires_at:
            raise PoolBridgeError('entitlement_expiry_missing', 503)
        limit = int(row['max_devices'] or 0)
        if limit not in (1, 2, 5):
            raise PoolBridgeError('plan_limit_mismatch', 403)

        decision = plan_resource(
            con,
            code=code,
            requesting_device_row_id=int(row['device_row_id']),
            limit=limit,
        )
        if decision.action == 'busy':
            raise PoolBridgeError('concurrent_limit_reached', 409)
        if decision.action == 'owned':
            assignment = assignment_for_device(con, device_row_id=int(row['device_row_id']))
            if assignment is None:
                raise PoolBridgeError('pool_assignment_missing', 409)
        elif decision.action == 'recycle':
            if decision.recyclable is None:
                raise PoolBridgeError('pool_assignment_race', 409)
            assignment = transfer_assignment(
                con,
                from_device_row_id=decision.recyclable.device_row_id,
                to_device_row_id=int(row['device_row_id']),
            )
        elif decision.action == 'allocate':
            assignment = prepare_assignment(
                formatted_code=code,
                device_id=safe_device_id,
                plan=plan,
                expires_at=expires_at,
            )
            try:
                save_device_pool_assignment_in_connection(
                    con, code, safe_device_id, assignment
                )
            except sqlite3.IntegrityError as exc:
                raise PoolBridgeError('pool_assignment_race', 409) from exc
        else:
            raise PoolBridgeError('pool_assignment_race', 409)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    if assignment is None:
        raise PoolBridgeError('pool_assignment_missing', 409)
    if assignment.get('pool_status') == 'pending' and assignment.get('pool_confirmation_token'):
        assignment['confirmation_required'] = True
        assignment = confirm_persisted_assignment(assignment)
    if assignment.get('pool_status') != 'active':
        raise PoolBridgeError('pool_assignment_not_active', 409)

    # Rebind is capacity-neutral and idempotent. It also repairs a previous
    # partial transfer where local ownership committed but the pool was briefly
    # unreachable before its subject metadata could be updated.
    assignment = rebind_assignment(
        assignment_id=int(assignment.get('pool_assignment_id') or 0),
        formatted_code=code,
        device_id=safe_device_id,
        plan=plan,
        expires_at=expires_at,
    )
    save_device_pool_assignment(code, safe_device_id, assignment)
    return assignment


def refresh_stored_assignment(raw_code: str, device_id: str) -> dict[str, Any]:
    """Refresh or acquire the resource currently needed by this installation."""
    if concurrent_sessions.enabled():
        return _refresh_concurrent_assignment(raw_code, device_id)

    from storage import (
        get_device_pool_assignment,
        get_device_pool_entitlement,
        save_device_pool_assignment,
    )

    entitlement = get_device_pool_entitlement(raw_code, device_id)
    if not entitlement:
        raise PoolBridgeError('device_not_registered', 403)

    stored = get_device_pool_assignment(entitlement['code'], device_id)
    if stored and stored.get('pool_status') == 'pending' and stored.get('pool_confirmation_token'):
        try:
            stored['confirmation_required'] = True
            return confirm_persisted_assignment(stored)
        except PoolBridgeError:
            pass

    prepared = prepare_assignment(
        formatted_code=entitlement['code'],
        device_id=device_id,
        plan=entitlement['plan'],
        expires_at=entitlement['expires_at'],
    )
    save_device_pool_assignment(entitlement['code'], device_id, prepared)
    return confirm_persisted_assignment(prepared)


def apply_stored_assignment_policy(
    raw_code: str,
    device_id: str,
    traffic_policy: str,
) -> dict[str, Any]:
    """Apply policy to the already assigned device without reserving a new pool slot."""

    from storage import get_device_pool_assignment, get_device_pool_entitlement

    policy = str(traffic_policy or '').strip().lower()
    if policy not in _VALID_TRAFFIC_POLICIES:
        raise PoolBridgeError('invalid_traffic_policy', 400)

    entitlement = get_device_pool_entitlement(raw_code, device_id)
    if not entitlement:
        raise PoolBridgeError('device_not_registered', 403)

    stored = get_device_pool_assignment(entitlement['code'], device_id)
    if not stored:
        raise PoolBridgeError('pool_assignment_missing', 409)
    if str(stored.get('pool_status') or '') != 'active':
        raise PoolBridgeError('pool_assignment_not_active', 409)

    assignment_id = int(stored.get('pool_assignment_id') or 0)
    if assignment_id <= 0:
        raise PoolBridgeError('pool_assignment_missing', 409)

    result = _request(
        '/api/v1/internal/pool/assignments/policy',
        {
            'assignment_id': assignment_id,
            'traffic_policy': policy,
        },
    )
    if result.get('ok') is not True:
        raise PoolBridgeError('traffic_policy_not_applied', 503)
    return result
