from __future__ import annotations

import hashlib
import hmac
import sqlite3
from datetime import datetime, timezone
from typing import Any

import config
import device_auth
from storage import parse_iso


_original_authorize_gateway_connection = device_auth.authorize_gateway_connection
_installed = False


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DATABASE_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _credential_epoch_from_fingerprint(fingerprint: str) -> str:
    safe = fingerprint.strip().lower()
    if len(safe) != 64 or any(ch not in "0123456789abcdef" for ch in safe):
        raise device_auth.DeviceAuthError("device_gate_not_authorized", 403)
    return hashlib.sha256(("skryon-gate-credential-v1:" + safe).encode("utf-8")).hexdigest()


def _current_row(assignment_id: int, device_id: str):
    con = _connect()
    try:
        return con.execute(
            """
            SELECT
                d.id AS device_row_id,
                d.code,
                d.public_key,
                d.public_key_fingerprint,
                d.active AS device_active,
                d.pool_status,
                d.pool_node_id,
                d.pool_gate_server_name,
                d.pool_gate_spki_sha256,
                c.status AS code_status,
                c.expires_at,
                c.max_devices,
                c.plan
            FROM code_devices AS d
            JOIN activation_codes AS c ON c.code = d.code
            WHERE d.pool_assignment_id = ? AND d.device_id = ?
            """,
            (int(assignment_id), device_id.strip()[:128]),
        ).fetchone()
    finally:
        con.close()


def _eligible(
    row: sqlite3.Row | None,
    *,
    node_id: int,
    gate_server_name: str,
    gate_spki_sha256: str,
) -> bool:
    if row is None:
        return False
    if (
        not bool(row["device_active"])
        or str(row["code_status"] or "") != "active"
        or str(row["pool_status"] or "") != "active"
        or int(row["pool_node_id"] or 0) != int(node_id)
        or str(row["pool_gate_server_name"] or "").strip().lower()
        != gate_server_name.strip().lower()
        or str(row["pool_gate_spki_sha256"] or "").strip().lower()
        != gate_spki_sha256.strip().lower()
        or not str(row["public_key"] or "").strip()
    ):
        return False

    expires_at = parse_iso(row["expires_at"])
    if not expires_at or expires_at <= datetime.now(timezone.utc):
        return False
    try:
        device_auth._plan_limit_and_title(
            str(row["plan"] or ""),
            int(row["max_devices"] or 1),
        )
    except device_auth.DeviceAuthError:
        return False
    return True


def _fingerprint_for_row(row: sqlite3.Row) -> str:
    stored = str(row["public_key_fingerprint"] or "").strip().lower()
    public_key = str(row["public_key"] or "").strip()
    if not public_key:
        raise device_auth.DeviceAuthError("device_gate_not_authorized", 403)
    _, decoded = device_auth._decode_public_key(public_key)
    if stored and not hmac.compare_digest(stored, decoded):
        raise device_auth.DeviceAuthError("device_gate_not_authorized", 403)
    return stored or decoded


def authorize_gateway_connection_guarded(**kwargs: Any) -> dict[str, Any]:
    result = _original_authorize_gateway_connection(**kwargs)

    assignment_id = int(kwargs.get("assignment_id") or 0)
    node_id = int(kwargs.get("node_id") or 0)
    device_id = str(kwargs.get("device_id") or "").strip()
    gate_server_name = str(kwargs.get("gate_server_name") or "").strip().lower()
    gate_spki_sha256 = str(kwargs.get("gate_spki_sha256") or "").strip().lower()

    row = _current_row(assignment_id, device_id)
    if not _eligible(
        row,
        node_id=node_id,
        gate_server_name=gate_server_name,
        gate_spki_sha256=gate_spki_sha256,
    ):
        raise device_auth.DeviceAuthError("device_gate_not_authorized", 403)
    assert row is not None

    # Close the race between the original authorization and this session epoch
    # lookup. The proof must still verify against the key that is current now.
    canonical = device_auth._gateway_canonical(
        assignment_id=assignment_id,
        node_id=node_id,
        gate_server_name=gate_server_name,
        gate_spki_sha256=gate_spki_sha256,
        device_id=device_id,
        server_issued_at=str(kwargs.get("server_issued_at") or ""),
        timestamp=str(kwargs.get("timestamp") or ""),
        server_nonce=str(kwargs.get("server_nonce") or ""),
        client_nonce=str(kwargs.get("client_nonce") or ""),
    )
    verified_fingerprint = device_auth._verify_signature(
        str(row["public_key"]),
        str(kwargs.get("signature_base64") or ""),
        canonical,
        str(kwargs.get("signature_algorithm") or ""),
    )
    current_fingerprint = _fingerprint_for_row(row)
    if not hmac.compare_digest(verified_fingerprint, current_fingerprint):
        raise device_auth.DeviceAuthError("device_gate_not_authorized", 403)

    guarded = dict(result)
    guarded["credential_epoch"] = _credential_epoch_from_fingerprint(current_fingerprint)
    return guarded


def check_gateway_session(
    *,
    assignment_id: int,
    node_id: int,
    gate_server_name: str,
    gate_spki_sha256: str,
    device_id: str,
    credential_epoch: str,
) -> dict[str, Any]:
    if assignment_id <= 0 or node_id <= 0:
        return {"allowed": False, "reason": "device_gate_session_invalid"}
    safe_device_id = device_id.strip()[:128]
    safe_server_name = gate_server_name.strip().lower()
    safe_spki = gate_spki_sha256.strip().lower()
    safe_epoch = credential_epoch.strip().lower()
    if (
        not safe_device_id
        or len(safe_epoch) != 64
        or any(ch not in "0123456789abcdef" for ch in safe_epoch)
    ):
        return {"allowed": False, "reason": "device_gate_session_invalid"}

    row = _current_row(assignment_id, safe_device_id)
    if not _eligible(
        row,
        node_id=node_id,
        gate_server_name=safe_server_name,
        gate_spki_sha256=safe_spki,
    ):
        return {"allowed": False, "reason": "device_gate_session_revoked"}
    assert row is not None

    try:
        current_epoch = _credential_epoch_from_fingerprint(_fingerprint_for_row(row))
    except device_auth.DeviceAuthError:
        return {"allowed": False, "reason": "device_gate_session_revoked"}
    if not hmac.compare_digest(current_epoch, safe_epoch):
        return {"allowed": False, "reason": "device_gate_session_replaced"}

    return {
        "allowed": True,
        "assignment_id": int(assignment_id),
        "node_id": int(node_id),
    }


def install_gate_session_guard() -> None:
    global _installed
    if _installed:
        return
    device_auth.authorize_gateway_connection = authorize_gateway_connection_guarded
    _installed = True
