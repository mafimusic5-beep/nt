from __future__ import annotations

import hmac
import sqlite3
import time
from typing import Any

import config
import device_auth
from device_recovery_common import _record_security_event, _revoke_recovery_key
from storage import format_code, now_iso

_original_authenticate = device_auth.authenticate_registered_device
_installed = False


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DATABASE_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _canonical(method, path, raw_code, device_id, timestamp, nonce, device_name, stored_name):
    if device_name is None:
        return device_auth._request_canonical(
            method=method, path=path, raw_code=raw_code, device_id=device_id,
            timestamp=timestamp, nonce=nonce,
        )
    name = device_name.strip().replace("\n", " ").replace("\r", " ")[:80]
    return device_auth._activation_canonical(
        path=path, raw_code=raw_code, device_id=device_id,
        device_name=name or stored_name or device_auth.DEFAULT_DEVICE_NAME,
        timestamp=timestamp, nonce=nonce,
    )


def _matches(public_key, fingerprint, canonical, signature, algorithm) -> bool:
    if not public_key or not fingerprint:
        return False
    try:
        verified = device_auth._verify_signature(public_key, signature, canonical, algorithm)
    except device_auth.DeviceAuthError:
        return False
    return hmac.compare_digest(verified, fingerprint)


def _watch(code: str, device_id: str):
    con = _connect()
    try:
        return con.execute(
            """
            SELECT w.*, d.public_key_fingerprint AS current_key_fingerprint,
                   d.device_name, d.active
            FROM device_recovery_watch AS w
            JOIN code_devices AS d ON d.id = w.device_row_id
            WHERE w.code = ? AND w.device_id = ?
            """,
            (format_code(code), device_id.strip()[:128]),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def _blocked_replacement(raw_code, method, path, device_id, timestamp, nonce, signature, algorithm, device_name) -> bool:
    canonical = None
    watch = _watch(raw_code, device_id)
    if watch and str(watch["state"]) == "conflict":
        canonical = _canonical(method, path, raw_code, device_id, timestamp, nonce, device_name, str(watch["device_name"] or ""))
        if _matches(str(watch["replacement_public_key"] or ""), str(watch["replacement_key_fingerprint"] or ""), canonical, signature, algorithm):
            return True

    con = _connect()
    try:
        rows = con.execute(
            """
            SELECT r.public_key, r.key_fingerprint, d.device_name
            FROM device_recovery_revoked_keys AS r
            JOIN code_devices AS d ON d.id = r.device_row_id
            WHERE d.code = ? AND d.device_id = ?
            """,
            (format_code(raw_code), device_id.strip()[:128]),
        ).fetchall()
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()
    for row in rows:
        canonical = canonical or _canonical(method, path, raw_code, device_id, timestamp, nonce, device_name, str(row["device_name"] or ""))
        if _matches(str(row["public_key"] or ""), str(row["key_fingerprint"] or ""), canonical, signature, algorithm):
            return True
    return False


def _restore_previous(raw_code, method, path, device_id, timestamp, nonce, signature, algorithm, device_name) -> dict[str, Any] | None:
    code = format_code(raw_code)
    safe_id = device_id.strip()[:128]
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        watch = con.execute(
            """
            SELECT w.*, d.public_key_fingerprint AS current_key_fingerprint,
                   d.device_name, d.active
            FROM device_recovery_watch AS w
            JOIN code_devices AS d ON d.id = w.device_row_id
            WHERE w.code = ? AND w.device_id = ?
            """,
            (code, safe_id),
        ).fetchone()
        if not watch or str(watch["state"]) != "watching" or not bool(watch["active"]):
            con.rollback()
            return None
        replacement = str(watch["replacement_key_fingerprint"] or "").strip()
        current = str(watch["current_key_fingerprint"] or "").strip()
        if not replacement or not hmac.compare_digest(replacement, current):
            raise device_auth.DeviceAuthError("device_recovery_conflict", 409)
        canonical = _canonical(method, path, raw_code, safe_id, timestamp, nonce, device_name, str(watch["device_name"] or ""))
        trusted = str(watch["trusted_key_fingerprint"] or "").strip()
        if not _matches(str(watch["trusted_public_key"] or ""), trusted, canonical, signature, algorithm):
            con.rollback()
            return None

        activation = device_auth._activation_row(con, raw_code)
        code = str(activation["code"])
        device_auth._consume_nonce(con, code=code, device_id=safe_id, nonce=nonce)
        now_epoch = int(time.time())
        con.execute(
            "UPDATE code_devices SET public_key = ?, public_key_fingerprint = ?, last_seen_at = ?, active = 1 WHERE id = ?",
            (str(watch["trusted_public_key"]), trusted, now_iso(), int(watch["device_row_id"])),
        )
        _revoke_recovery_key(
            con, device_row_id=int(watch["device_row_id"]),
            key_fingerprint=replacement,
            public_key=str(watch["replacement_public_key"] or ""),
        )
        changed = con.execute(
            "UPDATE device_recovery_watch SET state = 'conflict', conflict_at_epoch = ? WHERE device_row_id = ? AND state = 'watching'",
            (now_epoch, int(watch["device_row_id"])),
        )
        if changed.rowcount != 1:
            raise device_auth.DeviceAuthError("device_recovery_conflict", 409)
        con.execute(
            "UPDATE device_recovery_challenges SET consumed_at_epoch = COALESCE(consumed_at_epoch, ?) WHERE device_row_id = ?",
            (now_epoch, int(watch["device_row_id"])),
        )
        _record_security_event(con, code=code, device_row_id=int(watch["device_row_id"]), device_id=safe_id, event_type="trusted_key_returned", key_fingerprint=trusted)
        _record_security_event(con, code=code, device_row_id=int(watch["device_row_id"]), device_id=safe_id, event_type="replacement_key_revoked", key_fingerprint=replacement)
        payload = device_auth._profile_payload(con, activation=activation, current_device_id=safe_id)
        con.commit()
        return payload
    except device_auth.DeviceAuthError:
        con.rollback()
        raise
    except sqlite3.OperationalError:
        con.rollback()
        return None
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def authenticate_registered_device_guarded(*, raw_code: str, method: str, path: str, device_id: str, timestamp: str, nonce: str, signature_base64: str, signature_algorithm: str, device_name: str | None = None) -> dict[str, Any]:
    try:
        return _original_authenticate(
            raw_code=raw_code, method=method, path=path, device_id=device_id,
            timestamp=timestamp, nonce=nonce, signature_base64=signature_base64,
            signature_algorithm=signature_algorithm, device_name=device_name,
        )
    except device_auth.DeviceAuthError as error:
        if error.reason != "device_signature_invalid":
            raise

    if path != "/api/config/sync":
        raise device_auth.DeviceAuthError("device_signature_invalid", 401)
    if _blocked_replacement(raw_code, method, path, device_id, timestamp, nonce, signature_base64, signature_algorithm, device_name):
        raise device_auth.DeviceAuthError("not_bound", 200)
    restored = _restore_previous(raw_code, method, path, device_id, timestamp, nonce, signature_base64, signature_algorithm, device_name)
    if restored is None:
        raise device_auth.DeviceAuthError("device_signature_invalid", 401)
    return restored


def install_guard() -> None:
    global _installed
    if _installed:
        return
    device_auth.authenticate_registered_device = authenticate_registered_device_guarded
    _installed = True
