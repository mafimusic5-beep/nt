from __future__ import annotations

import hmac
import sqlite3
import time
from typing import Any

import config
import device_auth
from storage import format_code, now_iso

_original_authenticate = device_auth.authenticate_registered_device
_installed = False


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DATABASE_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    return con


def _canonical_for_request(
    *,
    method: str,
    path: str,
    raw_code: str,
    device_id: str,
    timestamp: str,
    nonce: str,
    device_name: str | None,
    stored_device_name: str,
) -> str:
    if device_name is None:
        return device_auth._request_canonical(
            method=method,
            path=path,
            raw_code=raw_code,
            device_id=device_id,
            timestamp=timestamp,
            nonce=nonce,
        )
    signed_name = device_name.strip().replace('\n', ' ').replace('\r', ' ')[:80]
    if not signed_name:
        signed_name = stored_device_name or device_auth.DEFAULT_DEVICE_NAME
    return device_auth._activation_canonical(
        path=path,
        raw_code=raw_code,
        device_id=device_id,
        device_name=signed_name,
        timestamp=timestamp,
        nonce=nonce,
    )


def _watch_for_device(code: str, device_id: str):
    con = _connect()
    try:
        return con.execute(
            '''
            SELECT
                w.device_row_id,
                w.trusted_public_key,
                w.trusted_key_fingerprint,
                w.replacement_public_key,
                w.replacement_key_fingerprint,
                w.state,
                d.public_key_fingerprint AS current_key_fingerprint,
                d.device_name,
                d.active
            FROM device_recovery_watch AS w
            JOIN code_devices AS d ON d.id = w.device_row_id
            WHERE w.code = ? AND w.device_id = ?
            ''',
            (format_code(code), device_id.strip()[:128]),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def _signature_matches_watch_key(
    *,
    public_key: str,
    expected_fingerprint: str,
    raw_code: str,
    method: str,
    path: str,
    device_id: str,
    timestamp: str,
    nonce: str,
    signature_base64: str,
    signature_algorithm: str,
    device_name: str | None,
    stored_device_name: str,
) -> bool:
    canonical = _canonical_for_request(
        method=method,
        path=path,
        raw_code=raw_code,
        device_id=device_id,
        timestamp=timestamp,
        nonce=nonce,
        device_name=device_name,
        stored_device_name=stored_device_name,
    )
    try:
        verified = device_auth._verify_signature(
            public_key,
            signature_base64,
            canonical,
            signature_algorithm,
        )
    except device_auth.DeviceAuthError:
        return False
    return bool(expected_fingerprint) and hmac.compare_digest(verified, expected_fingerprint)


def _replacement_key_returned_after_conflict(
    *,
    raw_code: str,
    method: str,
    path: str,
    device_id: str,
    timestamp: str,
    nonce: str,
    signature_base64: str,
    signature_algorithm: str,
    device_name: str | None,
) -> bool:
    watch = _watch_for_device(raw_code, device_id)
    if not watch or str(watch['state']) != 'conflict':
        return False
    return _signature_matches_watch_key(
        public_key=str(watch['replacement_public_key'] or ''),
        expected_fingerprint=str(watch['replacement_key_fingerprint'] or ''),
        raw_code=raw_code,
        method=method,
        path=path,
        device_id=device_id,
        timestamp=timestamp,
        nonce=nonce,
        signature_base64=signature_base64,
        signature_algorithm=signature_algorithm,
        device_name=device_name,
        stored_device_name=str(watch['device_name'] or ''),
    )


def _restore_if_original_key_returned(
    *,
    raw_code: str,
    method: str,
    path: str,
    device_id: str,
    timestamp: str,
    nonce: str,
    signature_base64: str,
    signature_algorithm: str,
    device_name: str | None,
) -> bool:
    code = format_code(raw_code)
    safe_device_id = device_id.strip()[:128]
    con = _connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        watch = con.execute(
            '''
            SELECT
                w.device_row_id,
                w.trusted_public_key,
                w.trusted_key_fingerprint,
                w.replacement_key_fingerprint,
                w.state,
                d.public_key_fingerprint AS current_key_fingerprint,
                d.device_name,
                d.active
            FROM device_recovery_watch AS w
            JOIN code_devices AS d ON d.id = w.device_row_id
            WHERE w.code = ? AND w.device_id = ?
            ''',
            (code, safe_device_id),
        ).fetchone()
        if not watch:
            con.rollback()
            return False
        if str(watch['state']) == 'conflict':
            raise device_auth.DeviceAuthError('device_recovery_security_lock', 423)
        if str(watch['state']) != 'watching' or not bool(watch['active']):
            con.rollback()
            return False

        current_fingerprint = str(watch['current_key_fingerprint'] or '').strip()
        replacement_fingerprint = str(watch['replacement_key_fingerprint'] or '').strip()
        if not replacement_fingerprint or not hmac.compare_digest(current_fingerprint, replacement_fingerprint):
            raise device_auth.DeviceAuthError('device_recovery_conflict', 409)

        canonical = _canonical_for_request(
            method=method,
            path=path,
            raw_code=raw_code,
            device_id=safe_device_id,
            timestamp=timestamp,
            nonce=nonce,
            device_name=device_name,
            stored_device_name=str(watch['device_name'] or ''),
        )
        verified = device_auth._verify_signature(
            str(watch['trusted_public_key']),
            signature_base64,
            canonical,
            signature_algorithm,
        )
        trusted_fingerprint = str(watch['trusted_key_fingerprint'] or '').strip()
        if not hmac.compare_digest(verified, trusted_fingerprint):
            con.rollback()
            return False

        now_epoch = int(time.time())
        con.execute(
            '''
            UPDATE code_devices
            SET public_key = ?, public_key_fingerprint = ?, last_seen_at = ?, active = 1
            WHERE id = ?
            ''',
            (
                str(watch['trusted_public_key']),
                trusted_fingerprint,
                now_iso(),
                int(watch['device_row_id']),
            ),
        )
        updated = con.execute(
            '''
            UPDATE device_recovery_watch
            SET state = 'conflict', conflict_at_epoch = ?
            WHERE device_row_id = ? AND state = 'watching'
            ''',
            (now_epoch, int(watch['device_row_id'])),
        )
        if updated.rowcount != 1:
            raise device_auth.DeviceAuthError('device_recovery_conflict', 409)
        con.execute(
            '''
            UPDATE device_recovery_challenges
            SET consumed_at_epoch = COALESCE(consumed_at_epoch, ?)
            WHERE device_row_id = ?
            ''',
            (now_epoch, int(watch['device_row_id'])),
        )
        con.commit()
        return True
    except device_auth.DeviceAuthError:
        con.rollback()
        raise
    except sqlite3.OperationalError:
        con.rollback()
        return False
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def authenticate_registered_device_guarded(
    *,
    raw_code: str,
    method: str,
    path: str,
    device_id: str,
    timestamp: str,
    nonce: str,
    signature_base64: str,
    signature_algorithm: str,
    device_name: str | None = None,
) -> dict[str, Any]:
    try:
        return _original_authenticate(
            raw_code=raw_code,
            method=method,
            path=path,
            device_id=device_id,
            timestamp=timestamp,
            nonce=nonce,
            signature_base64=signature_base64,
            signature_algorithm=signature_algorithm,
            device_name=device_name,
        )
    except device_auth.DeviceAuthError as error:
        if error.reason != 'device_signature_invalid':
            raise

    # Opening the Skryon home screen starts /api/config/sync. Only that explicit
    # app-sync event can make the old trusted key reclaim the slot. There is no
    # last_seen timeout, grace period, or seconds-based heuristic.
    if path != '/api/config/sync':
        raise device_auth.DeviceAuthError('device_signature_invalid', 401)

    # Once KEY_1 has returned and won the conflict, KEY_2 is deliberately mapped
    # to the existing client's fatal `not_bound` state. Current Android clients
    # then stop the tunnel and erase the synced VPN configuration on their next
    # config-sync response, while the server keeps the slot security-locked.
    if _replacement_key_returned_after_conflict(
        raw_code=raw_code,
        method=method,
        path=path,
        device_id=device_id,
        timestamp=timestamp,
        nonce=nonce,
        signature_base64=signature_base64,
        signature_algorithm=signature_algorithm,
        device_name=device_name,
    ):
        raise device_auth.DeviceAuthError('not_bound', 200)

    restored = _restore_if_original_key_returned(
        raw_code=raw_code,
        method=method,
        path=path,
        device_id=device_id,
        timestamp=timestamp,
        nonce=nonce,
        signature_base64=signature_base64,
        signature_algorithm=signature_algorithm,
        device_name=device_name,
    )
    if not restored:
        raise device_auth.DeviceAuthError('device_signature_invalid', 401)

    return _original_authenticate(
        raw_code=raw_code,
        method=method,
        path=path,
        device_id=device_id,
        timestamp=timestamp,
        nonce=nonce,
        signature_base64=signature_base64,
        signature_algorithm=signature_algorithm,
        device_name=device_name,
    )


def install_guard() -> None:
    global _installed
    if _installed:
        return
    device_auth.authenticate_registered_device = authenticate_registered_device_guarded
    _installed = True
