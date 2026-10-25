"""Server-side policy for Skryon's versioned, device-signed Windows client.

The version is bound into a signature made with the *registered device key*.
This stops unsigned callers, stale clients and replayed headers. It is NOT
hardware-backed proof that the executable is unmodified: a patched process on
a registered user's PC may still be able to call the signing key.
"""
from __future__ import annotations

import os

from fastapi import Request

from device_auth import DeviceAuthError

APP_VERSION_HEADER = "x-skryon-app-version-code"
PROOF_VERSION_HEADER = "x-skryon-proof-version"


def _minimum_version() -> int:
    raw = os.getenv("SKRYON_WINDOWS_MIN_VERSION_CODE", "20261026")
    try:
        return max(1, int(raw))
    except ValueError:
        # A misconfigured release policy must not silently disable checks.
        raise RuntimeError("Invalid SKRYON_WINDOWS_MIN_VERSION_CODE")


def signed_version(request: Request) -> int:
    """Require protocol v2 + bounded, supported client version, fail closed."""
    if request.headers.get(PROOF_VERSION_HEADER, "") != "2":
        raise DeviceAuthError("client_proof_upgrade_required", 426)
    raw = request.headers.get(APP_VERSION_HEADER, "")
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 12:
        raise DeviceAuthError("client_proof_upgrade_required", 426)
    version = int(raw)
    if version < _minimum_version():
        raise DeviceAuthError("upgrade_required", 426)
    # Optional release allowlist: set approved codes BEFORE distributing a
    # newer build. The client-supplied version remains a claim, not attestation.
    releases = os.getenv("SKRYON_WINDOWS_APPROVED_VERSION_CODES", "").strip()
    if releases:
        try:
            approved = {int(x.strip()) for x in releases.split(",") if x.strip()}
        except ValueError:
            raise RuntimeError("Invalid SKRYON_WINDOWS_APPROVED_VERSION_CODES")
        if version not in approved:
            raise DeviceAuthError("upgrade_required", 426)
    return version


def optional_lease_version(request: Request) -> int | None:
    """Keep Android/legacy session clients working until coordinated rollout.

    Enable SKRYON_REQUIRE_VERSIONED_LEASES=1 ONLY after all supported clients
    have shipped v2. Any partial v2 header always fails closed.
    """
    protocol = request.headers.get(PROOF_VERSION_HEADER)
    if protocol is not None or os.getenv("SKRYON_REQUIRE_VERSIONED_LEASES", "0") == "1":
        return signed_version(request)
    # Existing Android clients legitimately advertise a build version header
    # while signing protocol v1. Do not reject or elevate those unsigned
    # version claims until the coordinated v2-only rollout is enabled.
    return None


def public_listing_disabled() -> bool:
    """Switch off legacy unauthenticated city/node discovery after migration."""
    return os.getenv("SKRYON_PUBLIC_SERVER_LIST_DISABLED", "0") == "1"
