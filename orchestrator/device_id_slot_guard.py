from __future__ import annotations

import hmac
import sqlite3
from typing import Any, Dict

import device_auth
import device_recovery_actions
from device_recovery_common import (
    DEVICE_PROBE_RE,
    _connect,
    _migrate_device_id_if_needed,
    _resolve_device_row,
)
from storage import now_iso

_ORIGINAL_VALIDATE = device_auth.validate_device_registration
_ORIGINAL_REGISTER = device_auth.register_device
_INSTALLED = False


def _normalize_device_id(value: str) -> str:
    return value.strip().lower()[:128]


def _validate_identifier_shape(value: str) -> None:
    # Current Android builds use dp1:<sha256>. Keep legacy identifiers working
    # during migration, but reject malformed values that pretend to be dp1.
    if value.startswith("dp1:") and not DEVICE_PROBE_RE.fullmatch(value):
        raise device_auth.DeviceAuthError("device_identifier_invalid", 400)
    if len(value) < 4 or "\n" in value or "\r" in value:
        raise device_auth.DeviceAuthError("device_identifier_invalid", 400)


def _migrate_legacy_identifier_if_needed(raw_code: str, requested_device_id: str) -> None:
    if not DEVICE_PROBE_RE.fullmatch(requested_device_id):
        return
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        activation = device_auth._activation_row(con, raw_code)
        code = str(activation["code"])
        row, legacy_migration = _resolve_device_row(con, code, requested_device_id)
        if row is not None and legacy_migration:
            _migrate_device_id_if_needed(
                con,
                code=code,
                row=row,
                requested_device_id=requested_device_id,
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _validation_payload_for_existing(
    *,
    raw_code: str,
    device_id: str,
) -> Dict[str, Any]:
    con = device_auth._connect()
    try:
        activation = device_auth._activation_row(con, raw_code)
        code = str(activation["code"])
        limit, plan_title = device_auth._plan_limit_and_title(
            str(activation["plan"] or ""),
            int(activation["max_devices"] or 1),
        )
        existing = con.execute(
            "SELECT id, active FROM code_devices WHERE code = ? AND device_id = ?",
            (code, device_id),
        ).fetchone()
        if not existing or not bool(existing["active"]):
            raise device_auth.DeviceAuthError("device_not_registered", 403)
        active_count = int(
            con.execute(
                "SELECT COUNT(*) FROM code_devices WHERE code = ? AND active = 1",
                (code,),
            ).fetchone()[0]
        )
        return {
            "valid": True,
            "code": code,
            "already_registered": True,
            "plan_name": plan_title,
            "plan_code": activation["plan"] or "",
            "devices_used": active_count,
            "devices_limit": limit,
            "expires_at": activation["expires_at"],
        }
    finally:
        con.close()


def validate_device_registration(**kwargs):
    device_id = _normalize_device_id(str(kwargs.get("device_id") or ""))
    _validate_identifier_shape(device_id)
    _migrate_legacy_identifier_if_needed(str(kwargs.get("raw_code") or ""), device_id)
    kwargs["device_id"] = device_id
    try:
        return _ORIGINAL_VALIDATE(**kwargs)
    except device_auth.DeviceAuthError as exc:
        if exc.reason != "device_key_rotation_requires_reset":
            raise
        # Same activation code + same registered device identifier means the
        # same paid slot. A reinstall may present a fresh Keystore key without
        # consuming another slot or requiring Play Integrity recovery.
        return _validation_payload_for_existing(
            raw_code=str(kwargs.get("raw_code") or ""),
            device_id=device_id,
        )


def _rotate_key_for_existing_identifier(**kwargs) -> None:
    raw_code = str(kwargs.get("raw_code") or "")
    device_id = _normalize_device_id(str(kwargs.get("device_id") or ""))
    device_name = str(kwargs.get("device_name") or "").strip().replace("\n", " ").replace("\r", " ")[:80]
    public_key = str(kwargs.get("public_key_base64") or "")
    timestamp = str(kwargs.get("timestamp") or "")
    nonce = str(kwargs.get("nonce") or "")
    signature = str(kwargs.get("signature_base64") or "")
    algorithm = str(kwargs.get("signature_algorithm") or "")
    path = str(kwargs.get("path") or "")

    canonical = device_auth._activation_canonical(
        path=path,
        raw_code=raw_code,
        device_id=device_id,
        device_name=device_name,
        timestamp=timestamp,
        nonce=nonce,
    )
    fingerprint = device_auth._verify_signature(
        public_key,
        signature,
        canonical,
        algorithm,
    )

    con = device_auth._connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        activation = device_auth._activation_row(con, raw_code)
        code = str(activation["code"])
        row = con.execute(
            """
            SELECT id, active
            FROM code_devices
            WHERE code = ? AND device_id = ?
            """,
            (code, device_id),
        ).fetchone()
        if not row or not bool(row["active"]):
            raise device_auth.DeviceAuthError("device_not_registered", 403)

        safe_device_name = device_auth._public_device_name(device_name)
        current_time = now_iso()
        con.execute(
            """
            UPDATE code_devices
            SET device_name = ?,
                public_key = ?,
                public_key_fingerprint = ?,
                platform = 'android',
                app_version = ?,
                first_seen_at = COALESCE(first_seen_at, activated_at, ?),
                last_seen_at = ?,
                active = 1
            WHERE id = ?
            """,
            (
                safe_device_name,
                public_key,
                fingerprint,
                str(kwargs.get("app_version") or "").strip()[:32],
                current_time,
                current_time,
                int(row["id"]),
            ),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def register_device(**kwargs):
    device_id = _normalize_device_id(str(kwargs.get("device_id") or ""))
    _validate_identifier_shape(device_id)
    _migrate_legacy_identifier_if_needed(str(kwargs.get("raw_code") or ""), device_id)
    kwargs["device_id"] = device_id
    try:
        return _ORIGINAL_REGISTER(**kwargs)
    except device_auth.DeviceAuthError as exc:
        if exc.reason != "device_key_rotation_requires_reset":
            raise
        _rotate_key_for_existing_identifier(**kwargs)
        return _ORIGINAL_REGISTER(**kwargs)


def _identifier_recovery_challenge(_payload):
    # Recovery is intentionally unnecessary in the identifier-slot model.
    # The normal activation endpoint decides whether this identifier already
    # owns a slot or whether a new tariff slot must be consumed.
    return {
        "ok": True,
        "status": "not_needed",
        "recovered": False,
        "integrity_required": False,
    }


def install_device_id_slot_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    device_auth.validate_device_registration = validate_device_registration
    device_auth.register_device = register_device
    device_recovery_actions.recovery_challenge = _identifier_recovery_challenge
    _INSTALLED = True
