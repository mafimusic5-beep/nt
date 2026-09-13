from __future__ import annotations

from fastapi import APIRouter

import config
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
# Installing here makes every normal signed API request capable of proving that the
# original trusted key has returned. There is deliberately no time-window heuristic.
install_guard()

# Kept as a compatibility alias for tests and tooling that patch this module.
DATABASE_PATH = config.DATABASE_PATH

router = APIRouter(prefix="/api/device/recovery")
router.post("/challenge")(recovery_challenge)
router.post("/trusted-return")(trusted_return)
router.post("/confirm")(recovery_confirm)

__all__ = [
    "router",
    "recovery_challenge",
    "trusted_return",
    "recovery_confirm",
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
