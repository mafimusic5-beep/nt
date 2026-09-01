from __future__ import annotations

import base64
import hashlib
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from config import DATABASE_PATH, DEVICE_GATE_AUTH_MAX_SKEW_SECONDS
from pool_reservation_bridge import (
    PoolBridgeError,
    confirm_persisted_assignment,
    is_enabled as pool_bridge_enabled,
    prepare_assignment,
)
from storage import (
    format_code,
    now_iso,
    parse_iso,
    save_device_pool_assignment_in_connection,
)


MAX_CLOCK_SKEW_SECONDS = 300
NONCE_RETENTION_SECONDS = 24 * 60 * 60
SUPPORTED_SIGNATURE_ALGORITHM = 'SHA256withECDSA'
DEFAULT_DEVICE_NAME = 'Android-устройство'

_PLAN_LIMITS = {
    'personal': 1,
    'personal_plus': 2,
    'personalplus': 2,
    'family': 5,
    'личный': 1,
    'личный+': 2,
    'личныйплюс': 2,
    'семейный': 5,
}

_PLAN_TITLES = {
    1: 'Личный',
    2: 'Личный+',
    5: 'Семейный',
}

_TECHNICAL_DEVICE_NAME_MARKERS = (
    'sdk_gphone',
    'google sdk',
    'android sdk built for',
    'generic_x86',
    'generic x86',
    'x86_64',
    'arm64-v8a',
    'emulator',
)


@dataclass
class DeviceAuthError(Exception):
    reason: str
    status_code: int = 401

    def __str__(self) -> str:
        return self.reason


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    return con


def _column_names(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row['name']) for row in con.execute(f'PRAGMA table_info({table})').fetchall()}


def ensure_device_auth_storage() -> None:
    con = _connect()
    try:
        columns = _column_names(con, 'code_devices')
        additions = {
            'device_name': 'TEXT NOT NULL DEFAULT ""',
            'public_key': 'TEXT NOT NULL DEFAULT ""',
            'public_key_fingerprint': 'TEXT NOT NULL DEFAULT ""',
            'platform': 'TEXT NOT NULL DEFAULT "android"',
            'app_version': 'TEXT NOT NULL DEFAULT ""',
            'first_seen_at': 'TEXT',
            'last_seen_at': 'TEXT',
            'active': 'INTEGER NOT NULL DEFAULT 1',
            'pool_assignment_id': 'INTEGER',
            'pool_status': 'TEXT NOT NULL DEFAULT ""',
            'pool_confirmation_token': 'TEXT NOT NULL DEFAULT ""',
            'pool_node_id': 'INTEGER',
            'pool_node_name': 'TEXT NOT NULL DEFAULT ""',
            'pool_region': 'TEXT NOT NULL DEFAULT ""',
            'pool_config': 'TEXT NOT NULL DEFAULT ""',
            'pool_config_revision': 'INTEGER NOT NULL DEFAULT 0',
            'pool_speed_limit_mbps': 'INTEGER NOT NULL DEFAULT 0',
            'pool_client_port': 'INTEGER',
            'pool_gate_host': 'TEXT NOT NULL DEFAULT ""',
            'pool_gate_port': 'INTEGER',
            'pool_gate_server_name': 'TEXT NOT NULL DEFAULT ""',
            'pool_gate_spki_sha256': 'TEXT NOT NULL DEFAULT ""',
            'pool_entitlement_hash': 'TEXT NOT NULL DEFAULT ""',
            'pool_entitlement_expires_at': 'TEXT NOT NULL DEFAULT ""',
            'pool_updated_at': 'TEXT',
        }
        for column, definition in additions.items():
            if column not in columns:
                con.execute(f'ALTER TABLE code_devices ADD COLUMN {column} {definition}')

        con.execute(
            '''
            CREATE TABLE IF NOT EXISTS device_request_nonces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                device_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                created_at_epoch INTEGER NOT NULL,
                UNIQUE(code, nonce)
            )
            '''
        )
        con.execute(
            '''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_code_devices_key_fingerprint
            ON code_devices(code, public_key_fingerprint)
            WHERE public_key_fingerprint <> ''
            '''
        )
        con.execute(
            '''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_code_devices_pool_assignment
            ON code_devices(pool_assignment_id)
            WHERE pool_assignment_id IS NOT NULL
            '''
        )
        con.execute(
            '''
            UPDATE code_devices
            SET first_seen_at = COALESCE(first_seen_at, activated_at),
                last_seen_at = COALESCE(last_seen_at, activated_at),
                active = COALESCE(active, 1)
            '''
        )
        con.commit()
    finally:
        con.close()


def _normalized_plan(plan: str) -> str:
    return (
        str(plan or '')
        .strip()
        .lower()
        .replace('ё', 'е')
        .replace('-', '_')
        .replace(' ', '')
    )


def _plan_limit_and_title(plan: str, stored_limit: int) -> tuple[int, str]:
    normalized = _normalized_plan(plan)
    expected = _PLAN_LIMITS.get(normalized)
    if expected is None:
        if stored_limit not in _PLAN_TITLES:
            raise DeviceAuthError('plan_limit_mismatch', 403)
        expected = stored_limit
    if stored_limit != expected or expected not in _PLAN_TITLES:
        raise DeviceAuthError('plan_limit_mismatch', 403)
    return expected, _PLAN_TITLES[expected]


def _activation_row(con: sqlite3.Connection, raw_code: str) -> sqlite3.Row:
    formatted = format_code(raw_code)
    row = con.execute(
        '''
        SELECT code, status, expires_at, max_devices, plan
        FROM activation_codes
        WHERE code = ?
        ''',
        (formatted,),
    ).fetchone()
    if not row:
        raise DeviceAuthError('not_found', 401)
    if row['status'] != 'active':
        raise DeviceAuthError(str(row['status']), 403)

    expires_at = parse_iso(row['expires_at'])
    if expires_at and expires_at <= datetime.now(timezone.utc):
        con.execute(
            'UPDATE activation_codes SET status = ? WHERE code = ?',
            ('expired', formatted),
        )
        raise DeviceAuthError('expired', 403)

    _plan_limit_and_title(str(row['plan'] or ''), int(row['max_devices'] or 1))
    return row


def _timestamp_seconds(value: str) -> int:
    try:
        raw = int(str(value).strip())
    except (TypeError, ValueError):
        raise DeviceAuthError('device_timestamp_invalid', 401)
    return raw // 1000 if raw > 10_000_000_000 else raw


def _check_timestamp(value: str) -> None:
    _check_timestamp_with_skew(value, MAX_CLOCK_SKEW_SECONDS)


def _check_timestamp_with_skew(value: str, max_skew_seconds: int) -> None:
    timestamp = _timestamp_seconds(value)
    if abs(int(time.time()) - timestamp) > max_skew_seconds:
        raise DeviceAuthError('device_timestamp_invalid', 401)


def _decode_public_key(public_key_base64: str) -> tuple[ec.EllipticCurvePublicKey, str]:
    try:
        der = base64.b64decode(public_key_base64, validate=True)
        public_key = serialization.load_der_public_key(der)
    except Exception as exc:
        raise DeviceAuthError('device_public_key_invalid', 401) from exc
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise DeviceAuthError('device_public_key_invalid', 401)
    return public_key, hashlib.sha256(der).hexdigest()


def _verify_signature(
    public_key_base64: str,
    signature_base64: str,
    canonical: str,
    signature_algorithm: str,
) -> str:
    if signature_algorithm.strip() != SUPPORTED_SIGNATURE_ALGORITHM:
        raise DeviceAuthError('device_signature_algorithm_invalid', 401)
    public_key, fingerprint = _decode_public_key(public_key_base64)
    try:
        signature = base64.b64decode(signature_base64, validate=True)
        public_key.verify(
            signature,
            canonical.encode('utf-8'),
            ec.ECDSA(hashes.SHA256()),
        )
    except (ValueError, InvalidSignature) as exc:
        raise DeviceAuthError('device_signature_invalid', 401) from exc
    return fingerprint


def _auth_hash(raw_code: str) -> str:
    return hashlib.sha256(raw_code.strip().encode('utf-8')).hexdigest()


def _activation_canonical(
    *,
    path: str,
    raw_code: str,
    device_id: str,
    device_name: str,
    timestamp: str,
    nonce: str,
) -> str:
    return '\n'.join(
        (
            'method=POST',
            f'path={path}',
            f'device_id={device_id}',
            f'device_name={device_name}',
            f'timestamp={timestamp}',
            f'nonce={nonce}',
            f'auth_sha256={_auth_hash(raw_code)}',
        )
    )


def _request_canonical(
    *,
    method: str,
    path: str,
    raw_code: str,
    device_id: str,
    timestamp: str,
    nonce: str,
) -> str:
    return '\n'.join(
        (
            f'method={method.strip().upper()}',
            f'path={path}',
            f'device_id={device_id}',
            f'timestamp={timestamp}',
            f'nonce={nonce}',
            f'auth_sha256={_auth_hash(raw_code)}',
        )
    )


def _gateway_canonical(
    *,
    assignment_id: int,
    node_id: int,
    gate_server_name: str,
    gate_spki_sha256: str,
    device_id: str,
    server_issued_at: str,
    timestamp: str,
    server_nonce: str,
    client_nonce: str,
    protocol_version: int = 1,
    regional_policy: str = 'international',
    operation: str = 'connect',
) -> str:
    canonical = '\n'.join(
        (
            f'protocol=emery-device-gate-v{protocol_version}',
            f'assignment_id={assignment_id}',
            f'node_id={node_id}',
            f'gate_server_name={gate_server_name}',
            f'gate_spki_sha256={gate_spki_sha256}',
            f'device_id={device_id}',
            f'server_issued_at={server_issued_at}',
            f'timestamp={timestamp}',
            f'server_nonce={server_nonce}',
            f'client_nonce={client_nonce}',
        )
    )
    if protocol_version == 2:
        canonical += f'\nregional_policy={regional_policy}\noperation={operation}'
    return canonical


def _consume_nonce(
    con: sqlite3.Connection,
    *,
    code: str,
    device_id: str,
    nonce: str,
) -> None:
    safe_nonce = nonce.strip()
    if len(safe_nonce) < 16 or len(safe_nonce) > 128:
        raise DeviceAuthError('device_nonce_invalid', 401)
    cutoff = int(time.time()) - NONCE_RETENTION_SECONDS
    con.execute(
        'DELETE FROM device_request_nonces WHERE created_at_epoch < ?',
        (cutoff,),
    )
    try:
        con.execute(
            '''
            INSERT INTO device_request_nonces(code, device_id, nonce, created_at_epoch)
            VALUES (?, ?, ?, ?)
            ''',
            (code, device_id, safe_nonce, int(time.time())),
        )
    except sqlite3.IntegrityError as exc:
        raise DeviceAuthError('device_replay_detected', 401) from exc


def _public_device_name(value: str) -> str:
    normalized = ' '.join(str(value or '').replace('\n', ' ').replace('\r', ' ').split())[:64]
    if not normalized:
        return DEFAULT_DEVICE_NAME
    lowered = normalized.lower()
    if any(marker in lowered for marker in _TECHNICAL_DEVICE_NAME_MARKERS):
        return DEFAULT_DEVICE_NAME
    return normalized


def _looks_like_legacy_random_id(value: str) -> bool:
    try:
        return str(uuid.UUID(str(value).strip())) == str(value).strip().lower()
    except (ValueError, AttributeError, TypeError):
        return False


def _legacy_personal_slot_can_be_migrated(row: sqlite3.Row) -> bool:
    device_id = str(row['device_id'] or '').strip()
    device_name = str(row['device_name'] or '').strip().lower()
    has_technical_name = any(marker in device_name for marker in _TECHNICAL_DEVICE_NAME_MARKERS)
    missing_key = not str(row['public_key'] or '').strip()
    return _looks_like_legacy_random_id(device_id) or has_technical_name or missing_key


def _device_rows(con: sqlite3.Connection, code: str) -> list[sqlite3.Row]:
    return con.execute(
        '''
        SELECT
            device_id,
            device_name,
            platform,
            app_version,
            first_seen_at,
            last_seen_at,
            active
        FROM code_devices
        WHERE code = ?
        ORDER BY active DESC, last_seen_at DESC, id ASC
        ''',
        (code,),
    ).fetchall()


def _profile_payload(
    con: sqlite3.Connection,
    *,
    activation: sqlite3.Row,
    current_device_id: str,
) -> Dict[str, Any]:
    limit, plan_title = _plan_limit_and_title(
        str(activation['plan'] or ''),
        int(activation['max_devices'] or 1),
    )
    rows = _device_rows(con, str(activation['code']))
    active_rows = [row for row in rows if bool(row['active'])]
    current = next(
        (row for row in active_rows if row['device_id'] == current_device_id),
        None,
    )
    if current is None:
        raise DeviceAuthError('device_not_registered', 403)

    devices = [
        {
            'device_id': row['device_id'],
            'device_name': _public_device_name(str(row['device_name'] or '')),
            'platform': 'android',
            'app_version': '',
            'first_seen_at': row['first_seen_at'] or '',
            'last_seen_at': row['last_seen_at'] or '',
            'active': bool(row['active']),
            'is_current': row['device_id'] == current_device_id,
        }
        for row in rows
    ]
    return {
        'valid': True,
        'device_registered': True,
        'device_id': current_device_id,
        'device_name': _public_device_name(str(current['device_name'] or '')),
        'plan_name': plan_title,
        'plan_code': activation['plan'] or '',
        'devices_used': len(active_rows),
        'devices_limit': limit,
        'vpn_enabled': True,
        'router_enabled': False,
        'expires_at': activation['expires_at'],
        'devices': devices,
    }


def register_device(
    *,
    raw_code: str,
    path: str,
    device_id: str,
    device_name: str,
    public_key_base64: str,
    timestamp: str,
    nonce: str,
    signature_base64: str,
    signature_algorithm: str,
    platform: str,
    app_version: str,
) -> Dict[str, Any]:
    safe_device_id = device_id.strip()[:128]
    signed_device_name = device_name.strip().replace('\n', ' ').replace('\r', ' ')[:80]
    if not safe_device_id or not signed_device_name:
        raise DeviceAuthError('bad_request', 400)
    _check_timestamp(timestamp)

    canonical = _activation_canonical(
        path=path,
        raw_code=raw_code,
        device_id=safe_device_id,
        device_name=signed_device_name,
        timestamp=timestamp,
        nonce=nonce,
    )
    fingerprint = _verify_signature(
        public_key_base64,
        signature_base64,
        canonical,
        signature_algorithm,
    )
    safe_device_name = _public_device_name(signed_device_name)

    con = _connect()
    pool_assignment: Dict[str, Any] | None = None
    try:
        con.execute('BEGIN IMMEDIATE')
        activation = _activation_row(con, raw_code)
        code = str(activation['code'])
        limit, _ = _plan_limit_and_title(
            str(activation['plan'] or ''),
            int(activation['max_devices'] or 1),
        )
        _consume_nonce(con, code=code, device_id=safe_device_id, nonce=nonce)

        existing = con.execute(
            '''
            SELECT id, public_key, public_key_fingerprint, active
            FROM code_devices
            WHERE code = ? AND device_id = ?
            ''',
            (code, safe_device_id),
        ).fetchone()
        same_key_other_device = con.execute(
            '''
            SELECT device_id
            FROM code_devices
            WHERE code = ? AND public_key_fingerprint = ? AND device_id <> ?
            ''',
            (code, fingerprint, safe_device_id),
        ).fetchone()
        if same_key_other_device:
            raise DeviceAuthError('device_mismatch', 409)

        current_time = now_iso()
        if existing:
            if not bool(existing['active']):
                raise DeviceAuthError('device_revoked', 403)
            stored_public_key = str(existing['public_key'] or '').strip()
            stored_fingerprint = str(existing['public_key_fingerprint'] or '').strip()
            if stored_public_key:
                try:
                    _, decoded_fingerprint = _decode_public_key(stored_public_key)
                except DeviceAuthError as exc:
                    raise DeviceAuthError('device_key_binding_invalid', 409) from exc
                expected_fingerprint = stored_fingerprint or decoded_fingerprint
                if expected_fingerprint != fingerprint:
                    # Device ID and activation code are not sufficient to
                    # replace a registered key. A reinstall therefore needs an
                    # explicit support/admin reset instead of silently handing
                    # the existing VLESS assignment to a new private key.
                    raise DeviceAuthError('device_key_rotation_requires_reset', 409)
            con.execute(
                '''
                UPDATE code_devices
                SET device_name = ?,
                    public_key = ?,
                    public_key_fingerprint = ?,
                    platform = ?,
                    app_version = ?,
                    first_seen_at = COALESCE(first_seen_at, activated_at, ?),
                    last_seen_at = ?,
                    active = 1
                WHERE id = ?
                ''',
                (
                    safe_device_name,
                    public_key_base64,
                    fingerprint,
                    'android',
                    app_version.strip()[:32],
                    current_time,
                    current_time,
                    existing['id'],
                ),
            )
        else:
            active_rows = con.execute(
                '''
                SELECT id, device_id, device_name, public_key
                FROM code_devices
                WHERE code = ? AND active = 1
                ORDER BY last_seen_at DESC, id ASC
                ''',
                (code,),
            ).fetchall()
            if len(active_rows) >= limit:
                # Paid slots are immutable: neither a user action nor a new
                # installation silently replaces an already registered device.
                raise DeviceAuthError('device_limit_reached', 409)
            con.execute(
                '''
                INSERT INTO code_devices(
                    code,
                    device_id,
                    activated_at,
                    device_name,
                    public_key,
                    public_key_fingerprint,
                    platform,
                    app_version,
                    first_seen_at,
                    last_seen_at,
                    active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ''',
                (
                    code,
                    safe_device_id,
                    current_time,
                    safe_device_name,
                    public_key_base64,
                    fingerprint,
                    'android',
                    app_version.strip()[:32],
                    current_time,
                    current_time,
                ),
            )

        con.execute(
            '''
            UPDATE activation_codes
            SET device_id = CASE WHEN max_devices = 1 THEN ? ELSE COALESCE(device_id, ?) END,
                used_at = COALESCE(used_at, ?)
            WHERE code = ?
            ''',
            (safe_device_id, safe_device_id, current_time, code),
        )
        payload = _profile_payload(
            con,
            activation=activation,
            current_device_id=safe_device_id,
        )
        if pool_bridge_enabled():
            expires_at = str(activation['expires_at'] or '').strip()
            if not expires_at:
                raise DeviceAuthError('entitlement_expiry_missing', 503)
            try:
                pool_assignment = prepare_assignment(
                    formatted_code=code,
                    device_id=safe_device_id,
                    plan=str(activation['plan'] or ''),
                    expires_at=expires_at,
                )
            except PoolBridgeError as error:
                raise DeviceAuthError(error.reason, error.status_code) from error
            save_device_pool_assignment_in_connection(
                con,
                code,
                safe_device_id,
                pool_assignment,
            )
            payload['vpn_assignment'] = pool_assignment
        con.commit()
        if pool_assignment is not None:
            try:
                payload['vpn_assignment'] = confirm_persisted_assignment(pool_assignment)
            except PoolBridgeError as error:
                raise DeviceAuthError(error.reason, error.status_code) from error
        return payload
    except DeviceAuthError as error:
        if error.reason == 'expired':
            con.commit()
        else:
            con.rollback()
        raise
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def authenticate_registered_device(
    *,
    raw_code: str,
    method: str,
    path: str,
    device_id: str,
    timestamp: str,
    nonce: str,
    signature_base64: str,
    signature_algorithm: str,
    device_name: Optional[str] = None,
) -> Dict[str, Any]:
    safe_device_id = device_id.strip()[:128]
    if not safe_device_id:
        raise DeviceAuthError('device_not_registered', 401)
    _check_timestamp(timestamp)

    con = _connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        activation = _activation_row(con, raw_code)
        code = str(activation['code'])
        device = con.execute(
            '''
            SELECT id, device_name, public_key, active
            FROM code_devices
            WHERE code = ? AND device_id = ?
            ''',
            (code, safe_device_id),
        ).fetchone()
        if not device or not bool(device['active']) or not str(device['public_key'] or ''):
            raise DeviceAuthError('device_not_registered', 403)

        if device_name is None:
            canonical = _request_canonical(
                method=method,
                path=path,
                raw_code=raw_code,
                device_id=safe_device_id,
                timestamp=timestamp,
                nonce=nonce,
            )
        else:
            signed_name = device_name.strip().replace('\n', ' ').replace('\r', ' ')[:80]
            if not signed_name:
                signed_name = str(device['device_name'] or DEFAULT_DEVICE_NAME)
            canonical = _activation_canonical(
                path=path,
                raw_code=raw_code,
                device_id=safe_device_id,
                device_name=signed_name,
                timestamp=timestamp,
                nonce=nonce,
            )

        _verify_signature(
            str(device['public_key']),
            signature_base64,
            canonical,
            signature_algorithm,
        )
        _consume_nonce(con, code=code, device_id=safe_device_id, nonce=nonce)

        current_time = now_iso()
        con.execute(
            'UPDATE code_devices SET last_seen_at = ? WHERE id = ?',
            (current_time, device['id']),
        )
        payload = _profile_payload(
            con,
            activation=activation,
            current_device_id=safe_device_id,
        )
        con.commit()
        return payload
    except DeviceAuthError as error:
        if error.reason == 'expired':
            con.commit()
        else:
            con.rollback()
        raise
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def authorize_gateway_connection(
    *,
    assignment_id: int,
    node_id: int,
    gate_server_name: str,
    gate_spki_sha256: str,
    device_id: str,
    server_issued_at: str,
    timestamp: str,
    server_nonce: str,
    client_nonce: str,
    signature_base64: str,
    signature_algorithm: str,
    protocol_version: int = 1,
    regional_policy: str = 'international',
    operation: str = 'connect',
) -> Dict[str, Any]:
    """Authorize one TCP connection using the registered device key.

    The VLESS UUID is deliberately not accepted as authorization here. The
    client must prove possession of the Android Keystore key registered for
    this exact active pool assignment.
    """
    safe_device_id = device_id.strip()
    safe_gate_server_name = gate_server_name.strip().lower()
    safe_gate_spki_sha256 = gate_spki_sha256.strip().lower()
    safe_server_nonce = server_nonce.strip()
    safe_client_nonce = client_nonce.strip()
    if (
        (protocol_version, regional_policy, operation) not in {
            (1, 'international', 'connect'),
            (2, 'russia', 'connect'),
            (2, 'russia', 'check'),
        }
        or assignment_id <= 0
        or node_id <= 0
        or len(safe_gate_server_name) < 1
        or len(safe_gate_server_name) > 255
        or any(char in safe_gate_server_name for char in ('\n', '\r'))
        or len(safe_gate_spki_sha256) != 64
        or any(char not in '0123456789abcdef' for char in safe_gate_spki_sha256)
        or len(safe_device_id) < 4
        or len(safe_device_id) > 128
        or any(char in safe_device_id for char in ('\n', '\r'))
        or len(safe_server_nonce) < 16
        or len(safe_server_nonce) > 128
        or len(safe_client_nonce) < 16
        or len(safe_client_nonce) > 128
        or any(char in safe_server_nonce for char in ('\n', '\r'))
        or any(char in safe_client_nonce for char in ('\n', '\r'))
    ):
        raise DeviceAuthError('device_gate_proof_invalid', 401)

    _check_timestamp_with_skew(server_issued_at, DEVICE_GATE_AUTH_MAX_SKEW_SECONDS)
    _check_timestamp_with_skew(timestamp, DEVICE_GATE_AUTH_MAX_SKEW_SECONDS)
    canonical = _gateway_canonical(
        assignment_id=assignment_id,
        node_id=node_id,
        gate_server_name=safe_gate_server_name,
        gate_spki_sha256=safe_gate_spki_sha256,
        device_id=safe_device_id,
        server_issued_at=server_issued_at,
        timestamp=timestamp,
        server_nonce=safe_server_nonce,
        client_nonce=safe_client_nonce,
        protocol_version=protocol_version,
        regional_policy=regional_policy,
        operation=operation,
    )

    con = _connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute(
            '''
            SELECT
                d.id AS device_row_id,
                d.code,
                d.public_key,
                d.active AS device_active,
                d.pool_status,
                d.pool_node_id,
                d.pool_client_port,
                d.pool_gate_server_name,
                d.pool_gate_spki_sha256,
                c.status AS code_status,
                c.expires_at,
                c.max_devices,
                c.plan
            FROM code_devices AS d
            JOIN activation_codes AS c ON c.code = d.code
            WHERE d.pool_assignment_id = ? AND d.device_id = ?
            ''',
            (assignment_id, safe_device_id),
        ).fetchone()
        if not row:
            raise DeviceAuthError('device_gate_not_authorized', 403)
        if (
            not bool(row['device_active'])
            or str(row['code_status'] or '') != 'active'
            or str(row['pool_status'] or '') != 'active'
            or int(row['pool_node_id'] or 0) != node_id
            or str(row['pool_gate_server_name'] or '').strip().lower()
            != safe_gate_server_name
            or str(row['pool_gate_spki_sha256'] or '').strip().lower()
            != safe_gate_spki_sha256
            or not str(row['public_key'] or '').strip()
        ):
            raise DeviceAuthError('device_gate_not_authorized', 403)

        expires_at = parse_iso(row['expires_at'])
        if not expires_at or expires_at <= datetime.now(timezone.utc):
            if expires_at:
                con.execute(
                    'UPDATE activation_codes SET status = ? WHERE code = ?',
                    ('expired', str(row['code'])),
                )
            raise DeviceAuthError('device_gate_not_authorized', 403)
        _plan_limit_and_title(str(row['plan'] or ''), int(row['max_devices'] or 1))

        target_port = int(row['pool_client_port'] or 0)
        if target_port < 1 or target_port > 65535:
            raise DeviceAuthError('device_gate_target_unavailable', 503)

        _verify_signature(
            str(row['public_key']),
            signature_base64,
            canonical,
            signature_algorithm,
        )
        replay_nonce = 'gate-' + hashlib.sha256(
            (safe_server_nonce + '\0' + safe_client_nonce).encode('utf-8')
        ).hexdigest()
        _consume_nonce(
            con,
            code=str(row['code']),
            device_id=safe_device_id,
            nonce=replay_nonce,
        )
        con.execute(
            'UPDATE code_devices SET last_seen_at = ? WHERE id = ?',
            (now_iso(), int(row['device_row_id'])),
        )
        con.commit()
        result = {
            'allowed': True,
            'target_host': '127.0.0.2' if regional_policy == 'russia' else '127.0.0.1',
            'target_port': target_port,
            'assignment_id': assignment_id,
            'node_id': node_id,
        }
        if protocol_version == 2:
            # The gateway requires this echo. An old control plane which ignores
            # v2 fields must never silently authorize an unrestricted route.
            result.update(protocol_version=2, regional_policy=regional_policy, operation=operation)
        return result
    except DeviceAuthError:
        con.rollback()
        raise
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
