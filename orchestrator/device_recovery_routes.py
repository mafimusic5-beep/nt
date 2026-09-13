from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
from device_gate_session_guard import check_gateway_session, install_gate_session_guard
from device_security_guard import install_guard
from device_recovery_actions import recovery_challenge, recovery_confirm, trusted_return
from device_recovery_common import (
    DEVICE_PROBE_RE,
    LEGACY_ANDROID_ID_RE,
    RECOVERY_CHALLENGE_TTL_SECONDS,
    RECOVERY_PROTOCOL,
    RECOVERY_RATE_MAX_ATTEMPTS,
    RECOVERY_RATE_WINDOW_SECONDS,
    RecoveryChallengeRequest,
    RecoveryConfirmRequest,
    TrustedReturnRequest,
    _attempts,
    _auth_hash,
    _b64url_sha256,
    _challenge_canonical,
    _client_probe_from_legacy_android_id,
    _confirm_canonical,
    _connect,
    _ensure_storage,
    _integrity_binding_canonical,
    _migrate_device_id_if_needed,
    _rate_key,
    _rate_limited,
    _resolve_device_row,
    _stored_key_fingerprint,
    _trusted_return_canonical,
    _validate_confirm_challenge,
    _verify_new_key_challenge_proof,
    _verify_trusted_return_proof,
)

# checkout_routes imports this module before api.py binds the device-auth functions.
# Installing here makes normal signed requests capable of proving that the original
# trusted key has returned and makes new gate authorizations carry a credential epoch.
# Neither decision uses a last-seen or inactivity heuristic.
install_guard()
install_gate_session_guard()

# Kept as a compatibility alias for tests and tooling that patch this module.
DATABASE_PATH = config.DATABASE_PATH


class DeviceGateSessionCheckRequest(BaseModel):
    assignment_id: int = Field(gt=0)
    node_id: int = Field(gt=0)
    gate_server_name: str = Field(min_length=1, max_length=255)
    gate_spki_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    device_id: str = Field(min_length=4, max_length=128)
    credential_epoch: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


def device_gate_session_check(payload: DeviceGateSessionCheckRequest, request: Request):
    supplied_key = request.headers.get("x-device-gate-key", "").strip()
    expected_key = str(config.DEVICE_GATE_API_KEY or "").strip()
    if (
        len(expected_key) < 32
        or not supplied_key
        or not secrets.compare_digest(supplied_key, expected_key)
    ):
        return JSONResponse(
            status_code=403,
            content={"allowed": False, "reason": "device_gate_forbidden"},
        )
    return check_gateway_session(
        assignment_id=payload.assignment_id,
        node_id=payload.node_id,
        gate_server_name=payload.gate_server_name,
        gate_spki_sha256=payload.gate_spki_sha256,
        device_id=payload.device_id,
        credential_epoch=payload.credential_epoch,
    )


router = APIRouter()
router.post("/api/device/recovery/challenge")(recovery_challenge)
router.post("/api/device/recovery/trusted-return")(trusted_return)
router.post("/api/device/recovery/confirm")(recovery_confirm)
router.post("/internal/device-gate/session-check")(device_gate_session_check)

__all__ = [
    "router",
    "recovery_challenge",
    "trusted_return",
    "recovery_confirm",
    "device_gate_session_check",
    "DeviceGateSessionCheckRequest",
    "RecoveryChallengeRequest",
    "RecoveryConfirmRequest",
    "TrustedReturnRequest",
    "RECOVERY_PROTOCOL",
    "RECOVERY_CHALLENGE_TTL_SECONDS",
    "RECOVERY_RATE_WINDOW_SECONDS",
    "RECOVERY_RATE_MAX_ATTEMPTS",
    "DEVICE_PROBE_RE",
    "LEGACY_ANDROID_ID_RE",
    "_attempts",
    "_auth_hash",
    "_b64url_sha256",
    "_challenge_canonical",
    "_client_probe_from_legacy_android_id",
    "_confirm_canonical",
    "_connect",
    "_ensure_storage",
    "_integrity_binding_canonical",
    "_migrate_device_id_if_needed",
    "_rate_key",
    "_rate_limited",
    "_resolve_device_row",
    "_stored_key_fingerprint",
    "_trusted_return_canonical",
    "_validate_confirm_challenge",
    "_verify_new_key_challenge_proof",
    "_verify_trusted_return_proof",
]
