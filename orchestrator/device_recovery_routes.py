from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import device_auth
import play_integrity
from config import DATABASE_PATH
from device_identity_aliases import (
    derive_legacy_pool_subject_key,
    ensure_alias_storage,
    save_pool_subject_alias,
)
from storage import format_code, now_iso


router = APIRouter(prefix="/api/device/recovery")

RECOVERY_PROTOCOL = "skryon-device-recovery-v1"
RECOVERY_CHALLENGE_TTL_SECONDS = 120
RECOVERY_RATE_WINDOW_SECONDS = 10 * 60
RECOVERY_RATE_MAX_ATTEMPTS = 6
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


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _ensure_storage(con: sqlite3.Connection) -> None:
    ensure_alias_storage(con)
    con.execute(
        '''
        CREATE TABLE IF NOT EXISTS device_recovery_challenges (
            challenge_id TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            device_row_id INTEGER NOT NULL,
            old_device_id TEXT NOT NULL,
            requested_device_id TEXT NOT NULL,
            new_key_fingerprint TEXT NOT NULL,
            challenge_hash TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            created_at_epoch INTEGER NOT NULL,
            expires_at_epoch INTEGER NOT NULL,
            consumed_at_epoch INTEGER
        )
        '''
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_recovery_expiry ON device_recovery_challenges(expires_at_epoch)"
    )


def _json_error(reason: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": False, "reason": reason})


def _rate_key(code: str, device_id: str) -> str:
    return hashlib.sha256((format_code(code) + "\0" + device_id).encode("utf-8")).hexdigest()


def _rate_limited(code: str, device_id: str) -> bool:
    key = _rate_key(code, device_id)
    now = time.monotonic()
    bucket = _attempts[key]
    while bucket and now - bucket[0] > RECOVERY_RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RECOVERY_RATE_MAX_ATTEMPTS:
        return True
    bucket.append(now)
    return False


def _client_probe_from_legacy_android_id(value: str) -> str:
    normalized = value.strip().lower()
    digest = hashlib.sha256(("skryon-device-v1:" + normalized).encode("utf-8")).hexdigest()
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
            f"server_challenge_sha256={hashlib.sha256(server_challenge.encode('utf-8')).hexdigest()}",
            f"device_id={device_id}",
            f"new_key_sha256={new_key_fingerprint}",
            f"timestamp={timestamp}",
            f"nonce={nonce}",
            f"auth_sha256={_auth_hash(code)}",
            f"integrity_token_sha256={hashlib.sha256(integrity_token.encode('utf-8')).hexdigest()}",
        )
    )


def _resolve_device_row(con: sqlite3.Connection, code: str, requested_device_id: str) -> tuple[sqlite3.Row | None, bool]:
    exact = con.execute(
        '''
        SELECT id, device_id, public_key, public_key_fingerprint, active, pool_assignment_id
        FROM code_devices
        WHERE code = ? AND device_id = ?
        ''',
        (code, requested_device_id),
    ).fetchone()
    if exact:
        return exact, False

    if not DEVICE_PROBE_RE.fullmatch(requested_device_id):
        return None, False

    rows = con.execute(
        '''
        SELECT id, device_id, public_key, public_key_fingerprint, active, pool_assignment_id
        FROM code_devices
        WHERE code = ?
        ''',
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
        "SELECT id FROM code_devices WHERE code = ? AND device_id = ? AND id <> ?",
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
        "UPDATE activation_codes SET device_id = ? WHERE code = ? AND device_id = ?",
        (requested_device_id, code, old_device_id),
    )
    try:
        con.execute(
            "UPDATE device_request_nonces SET device_id = ? WHERE code = ? AND device_id = ?",
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
    verified_fingerprint = device_auth._verify_signature(
        payload.client_public_key,
        payload.signature,
        canonical,
        payload.signature_algorithm,
    )
    if not hmac.compare_digest(verified_fingerprint, fingerprint):
        raise device_auth.DeviceAuthError("device_signature_invalid", 401)
    return fingerprint


@router.post("/challenge")
def recovery_challenge(payload: RecoveryChallengeRequest):
    requested_device_id = payload.device_id.strip().lower()
    if not DEVICE_PROBE_RE.fullmatch(requested_device_id):
        return _json_error("device_recovery_probe_invalid", 400)
    if _rate_limited(payload.code, requested_device_id):
        return _json_error("too_many_attempts", 429)

    try:
        new_key_fingerprint = _verify_new_key_challenge_proof(payload)
        con = _connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            _ensure_storage(con)
            activation = device_auth._activation_row(con, payload.code)
            code = str(activation["code"])
            device_auth._consume_nonce(
                con,
                code=code,
                device_id=requested_device_id,
                nonce=payload.nonce,
            )
            row, legacy_migration = _resolve_device_row(con, code, requested_device_id)
            if row is None:
                con.commit()
                return {"ok": True, "status": "not_needed", "recovered": False, "integrity_required": False}
            if not bool(row["active"]):
                raise device_auth.DeviceAuthError("device_revoked", 403)

            old_key_fingerprint = _stored_key_fingerprint(row)
            if old_key_fingerprint and hmac.compare_digest(old_key_fingerprint, new_key_fingerprint):
                if legacy_migration:
                    _migrate_device_id_if_needed(
                        con,
                        code=code,
                        row=row,
                        requested_device_id=requested_device_id,
                    )
                con.execute(
                    "UPDATE code_devices SET last_seen_at = ? WHERE id = ?",
                    (now_iso(), int(row["id"])),
                )
                con.commit()
                return {
                    "ok": True,
                    "status": "migrated" if legacy_migration else "current",
                    "recovered": True,
                    "integrity_required": False,
                }

            challenge_id = secrets.token_urlsafe(24)
            server_challenge = secrets.token_urlsafe(32)
            challenge_hash = hashlib.sha256(server_challenge.encode("utf-8")).hexdigest()
            request_hash = _b64url_sha256(
                _integrity_binding_canonical(
                    challenge_id=challenge_id,
                    server_challenge=server_challenge,
                    device_id=requested_device_id,
                    new_key_fingerprint=new_key_fingerprint,
                    code=code,
                )
            )
            now_epoch = int(time.time())
            con.execute(
                "DELETE FROM device_recovery_challenges WHERE expires_at_epoch < ? OR consumed_at_epoch IS NOT NULL",
                (now_epoch - 3600,),
            )
            con.execute(
                '''
                INSERT INTO device_recovery_challenges(
                    challenge_id,
                    code,
                    device_row_id,
                    old_device_id,
                    requested_device_id,
                    new_key_fingerprint,
                    challenge_hash,
                    request_hash,
                    created_at_epoch,
                    expires_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    challenge_id,
                    code,
                    int(row["id"]),
                    str(row["device_id"]),
                    requested_device_id,
                    new_key_fingerprint,
                    challenge_hash,
                    request_hash,
                    now_epoch,
                    now_epoch + RECOVERY_CHALLENGE_TTL_SECONDS,
                ),
            )
            con.commit()
            return {
                "ok": True,
                "status": "challenge",
                "recovered": False,
                "integrity_required": True,
                "challenge_id": challenge_id,
                "server_challenge": server_challenge,
                "request_hash": request_hash,
                "expires_in": RECOVERY_CHALLENGE_TTL_SECONDS,
            }
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    except device_auth.DeviceAuthError as exc:
        return _json_error(exc.reason, exc.status_code)
    except Exception:
        return _json_error("device_recovery_failed", 500)


@router.post("/confirm")
def recovery_confirm(payload: RecoveryConfirmRequest):
    requested_device_id = payload.device_id.strip().lower()
    if not DEVICE_PROBE_RE.fullmatch(requested_device_id):
        return _json_error("device_recovery_probe_invalid", 400)
    if _rate_limited(payload.code, requested_device_id):
        return _json_error("too_many_attempts", 429)

    try:
        device_auth._check_timestamp_with_skew(payload.timestamp, 120)
        _, new_key_fingerprint = device_auth._decode_public_key(payload.client_public_key)
        confirm_canonical = _confirm_canonical(
            challenge_id=payload.challenge_id,
            server_challenge=payload.server_challenge,
            device_id=requested_device_id,
            new_key_fingerprint=new_key_fingerprint,
            code=payload.code,
            timestamp=payload.timestamp,
            nonce=payload.nonce,
            integrity_token=payload.integrity_token,
        )
        verified_fingerprint = device_auth._verify_signature(
            payload.client_public_key,
            payload.signature,
            confirm_canonical,
            payload.signature_algorithm,
        )
        if not hmac.compare_digest(verified_fingerprint, new_key_fingerprint):
            raise device_auth.DeviceAuthError("device_signature_invalid", 401)

        con = _connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            _ensure_storage(con)
            activation = device_auth._activation_row(con, payload.code)
            code = str(activation["code"])
            device_auth._consume_nonce(
                con,
                code=code,
                device_id=requested_device_id,
                nonce=payload.nonce,
            )
            row = con.execute(
                '''
                SELECT * FROM device_recovery_challenges
                WHERE challenge_id = ?
                ''',
                (payload.challenge_id.strip(),),
            ).fetchone()
            now_epoch = int(time.time())
            if not row:
                raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)
            if row["consumed_at_epoch"] is not None:
                raise device_auth.DeviceAuthError("device_recovery_replay_detected", 401)
            if int(row["expires_at_epoch"] or 0) < now_epoch:
                raise device_auth.DeviceAuthError("device_recovery_challenge_expired", 401)
            if str(row["code"]) != code:
                raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)
            if str(row["requested_device_id"]) != requested_device_id:
                raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)
            if not hmac.compare_digest(str(row["new_key_fingerprint"]), new_key_fingerprint):
                raise device_auth.DeviceAuthError("device_recovery_key_mismatch", 401)
            supplied_challenge_hash = hashlib.sha256(payload.server_challenge.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(str(row["challenge_hash"]), supplied_challenge_hash):
                raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)

            expected_request_hash = str(row["request_hash"])
            try:
                play_integrity.verify_standard_token(payload.integrity_token, expected_request_hash)
            except play_integrity.PlayIntegrityError as exc:
                transient = exc.args and str(exc.args[0]) in {
                    "play_integrity_not_configured",
                    "play_integrity_credentials_invalid",
                    "play_integrity_oauth_failed",
                    "play_integrity_decode_failed",
                }
                raise device_auth.DeviceAuthError(str(exc), 503 if transient else 403) from exc

            device = con.execute(
                '''
                SELECT id, device_id, public_key, public_key_fingerprint, active, pool_assignment_id
                FROM code_devices
                WHERE id = ? AND code = ?
                ''',
                (int(row["device_row_id"]), code),
            ).fetchone()
            if not device or not bool(device["active"]):
                raise device_auth.DeviceAuthError("device_revoked", 403)
            current_device_id = str(device["device_id"] or "")
            if current_device_id not in {str(row["old_device_id"]), requested_device_id}:
                raise device_auth.DeviceAuthError("device_recovery_conflict", 409)

            _migrate_device_id_if_needed(
                con,
                code=code,
                row=device,
                requested_device_id=requested_device_id,
            )
            con.execute(
                '''
                UPDATE code_devices
                SET public_key = ?,
                    public_key_fingerprint = ?,
                    last_seen_at = ?,
                    active = 1
                WHERE id = ?
                ''',
                (
                    payload.client_public_key,
                    new_key_fingerprint,
                    now_iso(),
                    int(device["id"]),
                ),
            )
            con.execute(
                "UPDATE device_recovery_challenges SET consumed_at_epoch = ? WHERE challenge_id = ?",
                (now_epoch, payload.challenge_id.strip()),
            )
            con.commit()
            return {
                "ok": True,
                "status": "recovered",
                "recovered": True,
                "integrity_required": True,
            }
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    except device_auth.DeviceAuthError as exc:
        return _json_error(exc.reason, exc.status_code)
    except Exception:
        return _json_error("device_recovery_failed", 500)
