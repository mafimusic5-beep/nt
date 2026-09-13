from __future__ import annotations

import hmac
import sqlite3

from fastapi import APIRouter

import device_auth
import device_recovery_routes as legacy
from storage import now_iso


router = APIRouter(prefix="/api/device/recovery")


def _active_rows(con: sqlite3.Connection, code: str) -> list[sqlite3.Row]:
    return con.execute(
        '''
        SELECT
            id,
            device_id,
            public_key,
            public_key_fingerprint,
            active,
            pool_assignment_id,
            activated_at,
            last_seen_at
        FROM code_devices
        WHERE code = ? AND active = 1
        ORDER BY COALESCE(last_seen_at, activated_at) ASC, id ASC
        ''',
        (code,),
    ).fetchall()


def _assert_key_is_not_bound_elsewhere(
    con: sqlite3.Connection,
    *,
    code: str,
    fingerprint: str,
    current_row_id: int | None,
) -> None:
    if not fingerprint:
        return
    params: tuple[object, ...]
    if current_row_id is None:
        sql = '''
            SELECT id
            FROM code_devices
            WHERE code = ? AND public_key_fingerprint = ?
            LIMIT 1
        '''
        params = (code, fingerprint)
    else:
        sql = '''
            SELECT id
            FROM code_devices
            WHERE code = ? AND public_key_fingerprint = ? AND id <> ?
            LIMIT 1
        '''
        params = (code, fingerprint, current_row_id)
    if con.execute(sql, params).fetchone():
        raise device_auth.DeviceAuthError("device_mismatch", 409)


def _bind_key(
    con: sqlite3.Connection,
    *,
    row_id: int,
    public_key: str,
    fingerprint: str,
) -> None:
    con.execute(
        '''
        UPDATE code_devices
        SET public_key = ?,
            public_key_fingerprint = ?,
            last_seen_at = ?,
            active = 1
        WHERE id = ?
        ''',
        (public_key, fingerprint, now_iso(), row_id),
    )


def _success(status: str, recovered: bool) -> dict:
    return {
        "ok": True,
        "status": status,
        "recovered": recovered,
        "integrity_required": False,
    }


@router.post("/challenge")
def recovery_challenge(payload: legacy.RecoveryChallengeRequest):
    """Restore access by the activation code without creating an extra paid slot.

    The activation code is the portable subscription credential.  Each app
    installation still proves possession of its own private key, but a lost
    installation key (reinstall, factory reset or a new phone) does not require
    a hardware identifier.  When all tariff slots are occupied, the least
    recently used active slot is rebound to the new installation atomically.
    """

    requested_device_id = payload.device_id.strip().lower()
    if not legacy.DEVICE_PROBE_RE.fullmatch(requested_device_id):
        return legacy._json_error("device_recovery_probe_invalid", 400)
    if legacy._rate_limited(payload.code, requested_device_id):
        return legacy._json_error("too_many_attempts", 429)

    try:
        new_key_fingerprint = legacy._verify_new_key_challenge_proof(payload)
        con = legacy._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            legacy._ensure_storage(con)
            activation = device_auth._activation_row(con, payload.code)
            code = str(activation["code"])
            device_auth._consume_nonce(
                con,
                code=code,
                device_id=requested_device_id,
                nonce=payload.nonce,
            )

            row, legacy_migration = legacy._resolve_device_row(
                con,
                code,
                requested_device_id,
            )
            _assert_key_is_not_bound_elsewhere(
                con,
                code=code,
                fingerprint=new_key_fingerprint,
                current_row_id=int(row["id"]) if row is not None else None,
            )

            if row is not None:
                if not bool(row["active"]):
                    raise device_auth.DeviceAuthError("device_revoked", 403)

                old_key_fingerprint = legacy._stored_key_fingerprint(row)
                same_key = bool(old_key_fingerprint) and hmac.compare_digest(
                    old_key_fingerprint,
                    new_key_fingerprint,
                )

                if legacy_migration:
                    legacy._migrate_device_id_if_needed(
                        con,
                        code=code,
                        row=row,
                        requested_device_id=requested_device_id,
                    )

                if same_key:
                    con.execute(
                        "UPDATE code_devices SET last_seen_at = ? WHERE id = ?",
                        (now_iso(), int(row["id"])),
                    )
                    con.commit()
                    return _success("migrated" if legacy_migration else "current", legacy_migration)

                _bind_key(
                    con,
                    row_id=int(row["id"]),
                    public_key=payload.client_public_key,
                    fingerprint=new_key_fingerprint,
                )
                con.commit()
                return _success("key_rotated", True)

            limit, _ = device_auth._plan_limit_and_title(
                str(activation["plan"] or ""),
                int(activation["max_devices"] or 1),
            )
            active_rows = _active_rows(con, code)

            if len(active_rows) < limit:
                con.commit()
                return _success("not_needed", False)

            replacement = active_rows[0]
            legacy._migrate_device_id_if_needed(
                con,
                code=code,
                row=replacement,
                requested_device_id=requested_device_id,
            )
            _bind_key(
                con,
                row_id=int(replacement["id"]),
                public_key=payload.client_public_key,
                fingerprint=new_key_fingerprint,
            )
            con.commit()
            return _success("slot_rebound", True)
        except device_auth.DeviceAuthError as error:
            if error.reason == "expired":
                con.commit()
            else:
                con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    except device_auth.DeviceAuthError as exc:
        return legacy._json_error(exc.reason, exc.status_code)
    except sqlite3.IntegrityError:
        return legacy._json_error("device_recovery_conflict", 409)
    except Exception:
        return legacy._json_error("device_recovery_failed", 500)


@router.post("/confirm")
def recovery_confirm(payload: legacy.RecoveryConfirmRequest):
    # Kept only so an already-issued legacy challenge can still finish during
    # a rolling deployment.  New challenges never require Play Integrity.
    return legacy.recovery_confirm(payload)
