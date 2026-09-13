from __future__ import annotations

import hashlib
import hmac
import time

from fastapi.responses import JSONResponse

import device_auth
import play_integrity
from device_recovery_common import (
    DEVICE_PROBE_RE,
    RECOVERY_CHALLENGE_TTL_SECONDS,
    RecoveryChallengeRequest,
    RecoveryConfirmRequest,
    TrustedReturnRequest,
    _b64url_sha256,
    _confirm_canonical,
    _connect,
    _ensure_storage,
    _integrity_binding_canonical,
    _migrate_device_id_if_needed,
    _rate_limited,
    _resolve_device_row,
    _stored_key_fingerprint,
    _validate_confirm_challenge,
    _verify_new_key_challenge_proof,
    _verify_trusted_return_proof,
)
from storage import now_iso


def _json_error(reason: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": False, "reason": reason})


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
            device_auth._consume_nonce(con, code=code, device_id=requested_device_id, nonce=payload.nonce)
            row, legacy_migration = _resolve_device_row(con, code, requested_device_id)
            if row is None:
                con.commit()
                return {"ok": True, "status": "not_needed", "recovered": False, "integrity_required": False}
            if not bool(row["active"]):
                raise device_auth.DeviceAuthError("device_revoked", 403)

            old_key_fingerprint = _stored_key_fingerprint(row)
            if old_key_fingerprint and hmac.compare_digest(old_key_fingerprint, new_key_fingerprint):
                if legacy_migration:
                    _migrate_device_id_if_needed(con, code=code, row=row, requested_device_id=requested_device_id)
                con.execute("UPDATE code_devices SET last_seen_at = ? WHERE id = ?", (now_iso(), int(row["id"])))
                con.commit()
                return {
                    "ok": True,
                    "status": "migrated" if legacy_migration else "current",
                    "recovered": True,
                    "integrity_required": False,
                }

            watch = con.execute(
                "SELECT state FROM device_recovery_watch WHERE device_row_id = ?",
                (int(row["id"]),),
            ).fetchone()
            if watch is not None:
                reason = "device_recovery_security_lock" if str(watch["state"]) == "conflict" else "device_recovery_watch_active"
                raise device_auth.DeviceAuthError(reason, 423)

            challenge_id = __import__("secrets").token_urlsafe(24)
            server_challenge = __import__("secrets").token_urlsafe(32)
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
                "DELETE FROM device_recovery_challenges WHERE expires_at_epoch < ? AND consumed_at_epoch IS NULL",
                (now_epoch - 3600,),
            )
            con.execute(
                '''
                INSERT INTO device_recovery_challenges(
                    challenge_id, code, device_row_id, old_device_id, requested_device_id,
                    new_key_fingerprint, old_key_fingerprint, challenge_hash, request_hash,
                    created_at_epoch, expires_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    challenge_id,
                    code,
                    int(row["id"]),
                    str(row["device_id"]),
                    requested_device_id,
                    new_key_fingerprint,
                    old_key_fingerprint,
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


def trusted_return(payload: TrustedReturnRequest):
    requested_device_id = payload.device_id.strip().lower()
    if not DEVICE_PROBE_RE.fullmatch(requested_device_id):
        return _json_error("device_recovery_probe_invalid", 400)
    if _rate_limited(payload.code, requested_device_id):
        return _json_error("too_many_attempts", 429)

    try:
        trusted_fingerprint = _verify_trusted_return_proof(payload)
        con = _connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            _ensure_storage(con)
            activation = device_auth._activation_row(con, payload.code)
            code = str(activation["code"])
            device = con.execute(
                '''
                SELECT id, device_id, public_key, public_key_fingerprint, active
                FROM code_devices WHERE code = ? AND device_id = ?
                ''',
                (code, requested_device_id),
            ).fetchone()
            if not device or not bool(device["active"]):
                raise device_auth.DeviceAuthError("device_not_registered", 403)

            watch = con.execute(
                "SELECT * FROM device_recovery_watch WHERE device_row_id = ?",
                (int(device["id"]),),
            ).fetchone()
            if not watch:
                raise device_auth.DeviceAuthError("device_trusted_return_unavailable", 403)
            if str(watch["state"]) != "watching":
                raise device_auth.DeviceAuthError("device_recovery_security_lock", 423)
            if str(watch["code"]) != code or str(watch["device_id"]) != requested_device_id:
                raise device_auth.DeviceAuthError("device_recovery_conflict", 409)
            if not hmac.compare_digest(str(watch["trusted_key_fingerprint"]), trusted_fingerprint):
                raise device_auth.DeviceAuthError("device_security_conflict", 403)

            current_fingerprint = _stored_key_fingerprint(device)
            if not hmac.compare_digest(str(watch["replacement_key_fingerprint"]), current_fingerprint):
                raise device_auth.DeviceAuthError("device_recovery_conflict", 409)

            device_auth._consume_nonce(con, code=code, device_id=requested_device_id, nonce=payload.nonce)
            now_epoch = int(time.time())
            con.execute(
                '''
                UPDATE code_devices
                SET public_key = ?, public_key_fingerprint = ?, last_seen_at = ?, active = 1
                WHERE id = ?
                ''',
                (
                    str(watch["trusted_public_key"]),
                    str(watch["trusted_key_fingerprint"]),
                    now_iso(),
                    int(device["id"]),
                ),
            )
            con.execute(
                "UPDATE device_recovery_watch SET state = 'conflict', conflict_at_epoch = ? WHERE device_row_id = ? AND state = 'watching'",
                (now_epoch, int(device["id"])),
            )
            con.execute(
                "UPDATE device_recovery_challenges SET consumed_at_epoch = COALESCE(consumed_at_epoch, ?) WHERE device_row_id = ?",
                (now_epoch, int(device["id"])),
            )
            con.commit()
            return {
                "ok": True,
                "status": "trusted_key_restored",
                "security_conflict": True,
                "recovery_locked": True,
            }
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    except device_auth.DeviceAuthError as exc:
        return _json_error(exc.reason, exc.status_code)
    except Exception:
        return _json_error("device_security_failed", 500)


def recovery_confirm(payload: RecoveryConfirmRequest):
    requested_device_id = payload.device_id.strip().lower()
    if not DEVICE_PROBE_RE.fullmatch(requested_device_id):
        return _json_error("device_recovery_probe_invalid", 400)
    if _rate_limited(payload.code, requested_device_id):
        return _json_error("too_many_attempts", 429)

    try:
        device_auth._check_timestamp_with_skew(payload.timestamp, 120)
        _, new_key_fingerprint = device_auth._decode_public_key(payload.client_public_key)
        canonical = _confirm_canonical(
            challenge_id=payload.challenge_id,
            server_challenge=payload.server_challenge,
            device_id=requested_device_id,
            new_key_fingerprint=new_key_fingerprint,
            code=payload.code,
            timestamp=payload.timestamp,
            nonce=payload.nonce,
            integrity_token=payload.integrity_token,
        )
        verified = device_auth._verify_signature(
            payload.client_public_key,
            payload.signature,
            canonical,
            payload.signature_algorithm,
        )
        if not hmac.compare_digest(verified, new_key_fingerprint):
            raise device_auth.DeviceAuthError("device_signature_invalid", 401)

        con = _connect()
        try:
            _ensure_storage(con)
            activation = device_auth._activation_row(con, payload.code)
            code = str(activation["code"])
            row = con.execute(
                "SELECT * FROM device_recovery_challenges WHERE challenge_id = ?",
                (payload.challenge_id.strip(),),
            ).fetchone()
            _validate_confirm_challenge(
                row,
                code=code,
                requested_device_id=requested_device_id,
                new_key_fingerprint=new_key_fingerprint,
                server_challenge=payload.server_challenge,
                now_epoch=int(time.time()),
            )
            expected_request_hash = str(row["request_hash"])
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

        try:
            play_integrity.verify_standard_token(payload.integrity_token, expected_request_hash)
        except play_integrity.PlayIntegrityError as exc:
            reason = str(exc)
            transient = reason in {
                "play_integrity_not_configured",
                "play_integrity_credentials_invalid",
                "play_integrity_oauth_failed",
                "play_integrity_decode_failed",
            }
            raise device_auth.DeviceAuthError(reason, 503 if transient else 403) from exc

        con = _connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            _ensure_storage(con)
            activation = device_auth._activation_row(con, payload.code)
            if str(activation["code"]) != code:
                raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)
            row = con.execute(
                "SELECT * FROM device_recovery_challenges WHERE challenge_id = ?",
                (payload.challenge_id.strip(),),
            ).fetchone()
            now_epoch = int(time.time())
            _validate_confirm_challenge(
                row,
                code=code,
                requested_device_id=requested_device_id,
                new_key_fingerprint=new_key_fingerprint,
                server_challenge=payload.server_challenge,
                now_epoch=now_epoch,
            )
            if not hmac.compare_digest(str(row["request_hash"]), expected_request_hash):
                raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)

            device = con.execute(
                '''
                SELECT id, device_id, public_key, public_key_fingerprint, active, pool_assignment_id
                FROM code_devices WHERE id = ? AND code = ?
                ''',
                (int(row["device_row_id"]), code),
            ).fetchone()
            if not device or not bool(device["active"]):
                raise device_auth.DeviceAuthError("device_revoked", 403)
            if con.execute(
                "SELECT 1 FROM device_recovery_watch WHERE device_row_id = ?",
                (int(device["id"]),),
            ).fetchone():
                raise device_auth.DeviceAuthError("device_recovery_watch_active", 423)

            current_device_id = str(device["device_id"] or "")
            if current_device_id not in {str(row["old_device_id"]), requested_device_id}:
                raise device_auth.DeviceAuthError("device_recovery_conflict", 409)
            expected_old_key = str(row["old_key_fingerprint"] or "").strip()
            current_old_key = _stored_key_fingerprint(device)
            if expected_old_key and not hmac.compare_digest(expected_old_key, current_old_key):
                raise device_auth.DeviceAuthError("device_recovery_conflict", 409)
            trusted_public_key = str(device["public_key"] or "").strip()
            if not trusted_public_key or not current_old_key:
                raise device_auth.DeviceAuthError("device_recovery_unavailable", 503)

            device_auth._consume_nonce(con, code=code, device_id=requested_device_id, nonce=payload.nonce)
            _migrate_device_id_if_needed(con, code=code, row=device, requested_device_id=requested_device_id)
            con.execute(
                '''
                INSERT OR REPLACE INTO device_recovery_watch(
                    device_row_id, code, device_id, trusted_public_key, trusted_key_fingerprint,
                    replacement_public_key, replacement_key_fingerprint, state, created_at_epoch, conflict_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'watching', ?, NULL)
                ''',
                (
                    int(device["id"]),
                    code,
                    requested_device_id,
                    trusted_public_key,
                    current_old_key,
                    payload.client_public_key,
                    new_key_fingerprint,
                    now_epoch,
                ),
            )
            con.execute(
                '''
                UPDATE code_devices
                SET public_key = ?, public_key_fingerprint = ?, last_seen_at = ?, active = 1
                WHERE id = ?
                ''',
                (payload.client_public_key, new_key_fingerprint, now_iso(), int(device["id"])),
            )
            updated = con.execute(
                "UPDATE device_recovery_challenges SET consumed_at_epoch = ? WHERE challenge_id = ? AND consumed_at_epoch IS NULL",
                (now_epoch, payload.challenge_id.strip()),
            )
            if updated.rowcount != 1:
                raise device_auth.DeviceAuthError("device_recovery_replay_detected", 401)
            con.commit()
            return {
                "ok": True,
                "status": "recovered",
                "recovered": True,
                "integrity_required": True,
                "trusted_key_watch": True,
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
