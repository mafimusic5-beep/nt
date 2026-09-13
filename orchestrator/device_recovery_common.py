from __future__ import annotations

import base64
import hashlib
import hmac
import re
import sqlite3
import time
from collections import defaultdict, deque
from typing import Deque

from pydantic import BaseModel, Field

import config
import device_auth
from device_identity_aliases import (
    derive_legacy_pool_subject_key,
    ensure_alias_storage,
    save_pool_subject_alias,
)
from storage import format_code

RECOVERY_PROTOCOL = "skryon-device-recovery-v1"
RECOVERY_CHALLENGE_TTL_SECONDS = 120
RECOVERY_RATE_WINDOW_SECONDS = 10 * 60
RECOVERY_RATE_MAX_ATTEMPTS = 6
SECURITY_EVENT_RETENTION_SECONDS = 90 * 24 * 60 * 60
DEVICE_PROBE_RE = re.compile(r"^dp1:[0-9a-f]{64}$")
LEGACY_ANDROID_ID_RE = re.compile(r"^[0-9a-f]{16}$")

_attempts: dict[str, Deque[float]] = defaultdict(deque)


class RecoveryChallengeRequest(BaseModel):
    code: str = Field(min_length=3, max_length=64)
    device_id: str = Field(min_length=4, max_length=128)
    client_public_key: str = Field(min_length=16, max_length=4096)
    timestamp: str = Field(min_length=8, max_length=32)
    nonce: str = Field(min_length=16, max_length=128)
    signature: str = Field(min_length=16, max_length=4096)
    signature_algorithm: str = Field(min_length=3, max_length=64)


class RecoveryConfirmRequest(RecoveryChallengeRequest):
    challenge_id: str = Field(min_length=16, max_length=128)
    server_challenge: str = Field(min_length=32, max_length=256)
    integrity_token: str = Field(min_length=32, max_length=20000)


class TrustedReturnRequest(RecoveryChallengeRequest):
    pass


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DATABASE_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _ensure_storage(con: sqlite3.Connection) -> None:
    ensure_alias_storage(con)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS device_recovery_challenges (
            challenge_id TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            device_row_id INTEGER NOT NULL,
            old_device_id TEXT NOT NULL,
            requested_device_id TEXT NOT NULL,
            new_key_fingerprint TEXT NOT NULL,
            old_key_fingerprint TEXT NOT NULL DEFAULT '',
            challenge_hash TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            created_at_epoch INTEGER NOT NULL,
            expires_at_epoch INTEGER NOT NULL,
            consumed_at_epoch INTEGER
        )
        """
    )
    columns = {
        str(row["name"])
        for row in con.execute("PRAGMA table_info(device_recovery_challenges)").fetchall()
    }
    if "old_key_fingerprint" not in columns:
        con.execute(
            "ALTER TABLE device_recovery_challenges "
            "ADD COLUMN old_key_fingerprint TEXT NOT NULL DEFAULT ''"
        )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_recovery_expiry "
        "ON device_recovery_challenges(expires_at_epoch)"
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS device_recovery_watch (
            device_row_id INTEGER PRIMARY KEY,
            code TEXT NOT NULL,
            device_id TEXT NOT NULL,
            trusted_public_key TEXT NOT NULL,
            trusted_key_fingerprint TEXT NOT NULL,
            replacement_public_key TEXT NOT NULL,
            replacement_key_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'watching',
            created_at_epoch INTEGER NOT NULL,
            conflict_at_epoch INTEGER
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_recovery_watch_code "
        "ON device_recovery_watch(code, device_id)"
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS device_recovery_revoked_keys (
            device_row_id INTEGER NOT NULL,
            key_fingerprint TEXT NOT NULL,
            public_key TEXT NOT NULL DEFAULT '',
            revoked_at_epoch INTEGER NOT NULL,
            reason TEXT NOT NULL DEFAULT 'security_conflict',
            PRIMARY KEY(device_row_id, key_fingerprint)
        )
        """
    )
    revoked_columns = {
        str(row["name"])
        for row in con.execute("PRAGMA table_info(device_recovery_revoked_keys)").fetchall()
    }
    if "public_key" not in revoked_columns:
        con.execute(
            "ALTER TABLE device_recovery_revoked_keys "
            "ADD COLUMN public_key TEXT NOT NULL DEFAULT ''"
        )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS device_security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT NOT NULL,
            device_row_id INTEGER,
            device_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            key_fingerprint TEXT NOT NULL DEFAULT '',
            created_at_epoch INTEGER NOT NULL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_security_events_created "
        "ON device_security_events(created_at_epoch)"
    )
    con.execute(
        "DELETE FROM device_security_events WHERE created_at_epoch < ?",
        (int(time.time()) - SECURITY_EVENT_RETENTION_SECONDS,),
    )


def _rate_key(
    code: str,
    device_id: str,
    scope: str = "recovery",
    key_fingerprint: str = "",
) -> str:
    material = "\0".join(
        (
            format_code(code),
            device_id.strip().lower(),
            scope.strip().lower(),
            key_fingerprint.strip().lower(),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _rate_limited(
    code: str,
    device_id: str,
    *,
    scope: str = "recovery",
    key_fingerprint: str = "",
) -> bool:
    key = _rate_key(code, device_id, scope, key_fingerprint)
    now = time.monotonic()
    bucket = _attempts[key]
    while bucket and now - bucket[0] > RECOVERY_RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RECOVERY_RATE_MAX_ATTEMPTS:
        return True
    bucket.append(now)
    return False


def _code_hash(code: str) -> str:
    return hashlib.sha256(format_code(code).encode("utf-8")).hexdigest()


def _record_security_event(
    con: sqlite3.Connection,
    *,
    code: str,
    device_row_id: int | None,
    device_id: str,
    event_type: str,
    key_fingerprint: str = "",
) -> None:
    con.execute(
        """
        INSERT INTO device_security_events(
            code_hash, device_row_id, device_id, event_type,
            key_fingerprint, created_at_epoch
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            _code_hash(code),
            device_row_id,
            device_id[:128],
            event_type[:64],
            key_fingerprint[:128],
            int(time.time()),
        ),
    )


def _is_revoked_recovery_key(
    con: sqlite3.Connection,
    *,
    device_row_id: int,
    key_fingerprint: str,
) -> bool:
    if not key_fingerprint:
        return False
    return (
        con.execute(
            """
            SELECT 1
            FROM device_recovery_revoked_keys
            WHERE device_row_id = ? AND key_fingerprint = ?
            """,
            (device_row_id, key_fingerprint),
        ).fetchone()
        is not None
    )


def _revoke_recovery_key(
    con: sqlite3.Connection,
    *,
    device_row_id: int,
    key_fingerprint: str,
    public_key: str = "",
    reason: str = "security_conflict",
) -> None:
    if not key_fingerprint:
        return
    con.execute(
        """
        INSERT INTO device_recovery_revoked_keys(
            device_row_id, key_fingerprint, public_key,
            revoked_at_epoch, reason
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(device_row_id, key_fingerprint) DO UPDATE SET
            public_key = CASE
                WHEN excluded.public_key <> '' THEN excluded.public_key
                ELSE device_recovery_revoked_keys.public_key
            END,
            revoked_at_epoch = excluded.revoked_at_epoch,
            reason = excluded.reason
        """,
        (
            device_row_id,
            key_fingerprint,
            public_key,
            int(time.time()),
            reason[:64],
        ),
    )


def _client_probe_from_legacy_android_id(value: str) -> str:
    normalized = value.strip().lower()
    digest = hashlib.sha256(
        ("skryon-device-v1:" + normalized).encode("utf-8")
    ).hexdigest()
    return "dp1:" + digest


def _auth_hash(code: str) -> str:
    return hashlib.sha256(format_code(code).encode("utf-8")).hexdigest()


def _b64url_sha256(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _challenge_canonical(
    *,
    device_id: str,
    new_key_fingerprint: str,
    code: str,
    timestamp: str,
    nonce: str,
) -> str:
    return "\n".join(
        (
            f"protocol={RECOVERY_PROTOCOL}",
            "stage=challenge",
            "path=/api/device/recovery/challenge",
            f"device_id={device_id}",
            f"new_key_sha256={new_key_fingerprint}",
            f"timestamp={timestamp}",
            f"nonce={nonce}",
            f"auth_sha256={_auth_hash(code)}",
        )
    )


def _trusted_return_canonical(
    *,
    device_id: str,
    trusted_key_fingerprint: str,
    code: str,
    timestamp: str,
    nonce: str,
) -> str:
    return "\n".join(
        (
            f"protocol={RECOVERY_PROTOCOL}",
            "stage=trusted-return",
            "path=/api/device/recovery/trusted-return",
            f"device_id={device_id}",
            f"trusted_key_sha256={trusted_key_fingerprint}",
            f"timestamp={timestamp}",
            f"nonce={nonce}",
            f"auth_sha256={_auth_hash(code)}",
        )
    )


def _integrity_binding_canonical(
    *,
    challenge_id: str,
    server_challenge: str,
    device_id: str,
    new_key_fingerprint: str,
    code: str,
) -> str:
    return "\n".join(
        (
            f"protocol={RECOVERY_PROTOCOL}",
            f"challenge_id={challenge_id}",
            f"server_challenge={server_challenge}",
            f"device_id={device_id}",
            f"new_key_sha256={new_key_fingerprint}",
            f"auth_sha256={_auth_hash(code)}",
        )
    )


def _confirm_canonical(
    *,
    challenge_id: str,
    server_challenge: str,
    device_id: str,
    new_key_fingerprint: str,
    code: str,
    timestamp: str,
    nonce: str,
    integrity_token: str,
) -> str:
    return "\n".join(
        (
            f"protocol={RECOVERY_PROTOCOL}",
            "stage=confirm",
            "path=/api/device/recovery/confirm",
            f"challenge_id={challenge_id}",
            "server_challenge_sha256="
            + hashlib.sha256(server_challenge.encode("utf-8")).hexdigest(),
            f"device_id={device_id}",
            f"new_key_sha256={new_key_fingerprint}",
            f"timestamp={timestamp}",
            f"nonce={nonce}",
            f"auth_sha256={_auth_hash(code)}",
            "integrity_token_sha256="
            + hashlib.sha256(integrity_token.encode("utf-8")).hexdigest(),
        )
    )


def _resolve_device_row(
    con: sqlite3.Connection,
    code: str,
    requested_device_id: str,
) -> tuple[sqlite3.Row | None, bool]:
    exact = con.execute(
        """
        SELECT id, device_id, public_key, public_key_fingerprint,
               active, pool_assignment_id
        FROM code_devices
        WHERE code = ? AND device_id = ?
        """,
        (code, requested_device_id),
    ).fetchone()
    if exact:
        return exact, False
    if not DEVICE_PROBE_RE.fullmatch(requested_device_id):
        return None, False
    rows = con.execute(
        """
        SELECT id, device_id, public_key, public_key_fingerprint,
               active, pool_assignment_id
        FROM code_devices
        WHERE code = ?
        """,
        (code,),
    ).fetchall()
    for row in rows:
        legacy_id = str(row["device_id"] or "").strip().lower()
        if LEGACY_ANDROID_ID_RE.fullmatch(legacy_id) and hmac.compare_digest(
            _client_probe_from_legacy_android_id(legacy_id),
            requested_device_id,
        ):
            return row, True
    return None, False


def _stored_key_fingerprint(row: sqlite3.Row) -> str:
    stored = str(row["public_key_fingerprint"] or "").strip()
    if stored:
        return stored
    public_key = str(row["public_key"] or "").strip()
    if not public_key:
        return ""
    _, fingerprint = device_auth._decode_public_key(public_key)
    return fingerprint


def _migrate_device_id_if_needed(
    con: sqlite3.Connection,
    *,
    code: str,
    row: sqlite3.Row,
    requested_device_id: str,
) -> None:
    old_device_id = str(row["device_id"] or "").strip()
    if old_device_id == requested_device_id:
        return
    conflict = con.execute(
        "SELECT id FROM code_devices "
        "WHERE code = ? AND device_id = ? AND id <> ?",
        (code, requested_device_id, int(row["id"])),
    ).fetchone()
    if conflict:
        raise device_auth.DeviceAuthError("device_recovery_conflict", 409)
    if row["pool_assignment_id"] is not None:
        preserved_subject_key = derive_legacy_pool_subject_key(code, old_device_id)
        if not preserved_subject_key:
            raise device_auth.DeviceAuthError("device_recovery_unavailable", 503)
        save_pool_subject_alias(
            con,
            code=code,
            device_id=requested_device_id,
            subject_key=preserved_subject_key,
            created_at_epoch=int(time.time()),
        )
    con.execute(
        "UPDATE code_devices SET device_id = ? WHERE id = ?",
        (requested_device_id, int(row["id"])),
    )
    con.execute(
        "UPDATE activation_codes SET device_id = ? "
        "WHERE code = ? AND device_id = ?",
        (requested_device_id, code, old_device_id),
    )
    try:
        con.execute(
            "UPDATE device_request_nonces SET device_id = ? "
            "WHERE code = ? AND device_id = ?",
            (requested_device_id, code, old_device_id),
        )
    except sqlite3.OperationalError:
        pass


def _verify_new_key_challenge_proof(payload: RecoveryChallengeRequest) -> str:
    device_auth._check_timestamp_with_skew(payload.timestamp, 120)
    _, fingerprint = device_auth._decode_public_key(payload.client_public_key)
    canonical = _challenge_canonical(
        device_id=payload.device_id,
        new_key_fingerprint=fingerprint,
        code=payload.code,
        timestamp=payload.timestamp,
        nonce=payload.nonce,
    )
    verified = device_auth._verify_signature(
        payload.client_public_key,
        payload.signature,
        canonical,
        payload.signature_algorithm,
    )
    if not hmac.compare_digest(verified, fingerprint):
        raise device_auth.DeviceAuthError("device_signature_invalid", 401)
    return fingerprint


def _verify_trusted_return_proof(payload: TrustedReturnRequest) -> str:
    device_auth._check_timestamp_with_skew(payload.timestamp, 120)
    _, fingerprint = device_auth._decode_public_key(payload.client_public_key)
    canonical = _trusted_return_canonical(
        device_id=payload.device_id,
        trusted_key_fingerprint=fingerprint,
        code=payload.code,
        timestamp=payload.timestamp,
        nonce=payload.nonce,
    )
    verified = device_auth._verify_signature(
        payload.client_public_key,
        payload.signature,
        canonical,
        payload.signature_algorithm,
    )
    if not hmac.compare_digest(verified, fingerprint):
        raise device_auth.DeviceAuthError("device_signature_invalid", 401)
    return fingerprint


def _validate_confirm_challenge(
    row: sqlite3.Row | None,
    *,
    code: str,
    requested_device_id: str,
    new_key_fingerprint: str,
    server_challenge: str,
    now_epoch: int,
) -> None:
    if not row:
        raise device_auth.DeviceAuthError(
            "device_recovery_challenge_invalid",
            401,
        )
    if row["consumed_at_epoch"] is not None:
        raise device_auth.DeviceAuthError(
            "device_recovery_replay_detected",
            401,
        )
    if int(row["expires_at_epoch"] or 0) < now_epoch:
        raise device_auth.DeviceAuthError(
            "device_recovery_challenge_expired",
            401,
        )
    if (
        str(row["code"]) != code
        or str(row["requested_device_id"]) != requested_device_id
    ):
        raise device_auth.DeviceAuthError(
            "device_recovery_challenge_invalid",
            401,
        )
    if not hmac.compare_digest(
        str(row["new_key_fingerprint"]),
        new_key_fingerprint,
    ):
        raise device_auth.DeviceAuthError(
            "device_recovery_key_mismatch",
            401,
        )
    supplied_hash = hashlib.sha256(
        server_challenge.encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(str(row["challenge_hash"]), supplied_hash):
        raise device_auth.DeviceAuthError(
            "device_recovery_challenge_invalid",
            401,
        )
