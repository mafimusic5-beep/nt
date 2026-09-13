from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Dict

import device_auth
from device_identity_aliases import (
    derive_legacy_pool_subject_key,
    ensure_alias_storage,
    save_pool_subject_alias,
)
from storage import now_iso

DEVICE_PROBE_RE = re.compile(r"^dp1:[0-9a-f]{64}$")
LEGACY_ANDROID_ID_RE = re.compile(r"^[0-9a-f]{16}$")

_ORIGINAL_VALIDATE = device_auth.validate_device_registration
_ORIGINAL_REGISTER = device_auth.register_device
_INSTALLED = False


def _normalize_device_id(value: str) -> str:
    return value.strip().lower()[:128]


def _validate_identifier_shape(value: str) -> None:
    # Current Android builds use dp1:<sha256>. Keep already-supported legacy
    # identifiers working during migration, but reject malformed dp1 values.
    if value.startswith("dp1:") and not DEVICE_PROBE_RE.fullmatch(value):
        raise device_auth.DeviceAuthError("device_identifier_invalid", 400)
    if len(value) < 4 or "\n" in value or "\r" in value:
        raise device_auth.DeviceAuthError("device_identifier_invalid", 400)


def _probe_from_legacy_android_id(value: str) -> str:
    normalized = value.strip().lower()
    digest = hashlib.sha256(
        ("skryon-device-v1:" + normalized).encode("utf-8")
    ).hexdigest()
    return "dp1:" + digest


def _migrate_legacy_identifier_if_needed(raw_code: str, requested_device_id: str) -> None:
    if not DEVICE_PROBE_RE.fullmatch(requested_device_id):
        return

    con = device_auth._connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        ensure_alias_storage(con)
        activation = device_auth._activation_row(con, raw_code)
        code = str(activation["code"])

        exact = con.execute(
            "SELECT id FROM code_devices WHERE code = ? AND device_id = ?",
            (code, requested_device_id),
        ).fetchone()
        if exact:
            con.commit()
            return

        rows = con.execute(
            """
            SELECT id, device_id, pool_assignment_id
            FROM code_devices
            WHERE code = ?
            """,
            (code,),
        ).fetchall()
        target = None
        for row in rows:
            legacy_id = str(row["device_id"] or "").strip().lower()
            if LEGACY_ANDROID_ID_RE.fullmatch(legacy_id) and hmac.compare_digest(
                _probe_from_legacy_android_id(legacy_id),
                requested_device_id,
            ):
                target = row
                break

        if target is None:
            con.commit()
            return

        old_device_id = str(target["device_id"] or "").strip()
        if target["pool_assignment_id"] is not None:
            preserved_subject_key = derive_legacy_pool_subject_key(code, old_device_id)
            if preserved_subject_key:
                save_pool_subject_alias(
                    con,
                    code=code,
                    device_id=requested_device_id,
                    subject_key=preserved_subject_key,
                    created_at_epoch=int(__import__("time").time()),
                )

        con.execute(
            "UPDATE code_devices SET device_id = ? WHERE id = ?",
            (requested_device_id, int(target["id"])),
        )
        con.execute(
            """
            UPDATE activation_codes
            SET device_id = ?
            WHERE code = ? AND device_id = ?
            """,
            (requested_device_id, code, old_device_id),
        )
        try:
            con.execute(
                """
                UPDATE device_request_nonces
                SET device_id = ?
                WHERE code = ? AND device_id = ?
                """,
                (requested_device_id, code, old_device_id),
            )
        except Exception:
            pass
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
        if (
            exc.reason != "device_key_rotation_requires_reset"
            or not DEVICE_PROBE_RE.fullmatch(device_id)
        ):
            raise
        # Only the current valid pseudonymous Android identifier may reuse an
        # existing paid slot with a fresh installation key. Legacy/random IDs
        # retain the stricter old-key behavior.
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
        if (
            exc.reason != "device_key_rotation_requires_reset"
            or not DEVICE_PROBE_RE.fullmatch(device_id)
        ):
            raise
        _rotate_key_for_existing_identifier(**kwargs)
        return _ORIGINAL_REGISTER(**kwargs)


def install_device_id_slot_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    device_auth.validate_device_registration = validate_device_registration
    device_auth.register_device = register_device
    _INSTALLED = True
