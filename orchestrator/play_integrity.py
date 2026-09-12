from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


PLAY_INTEGRITY_SCOPE = "https://www.googleapis.com/auth/playintegrity"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_PACKAGE_NAME = "com.skryon.shield"
DEFAULT_MAX_TOKEN_AGE_MILLIS = 2 * 60 * 1000


class PlayIntegrityError(RuntimeError):
    pass


_access_token_lock = threading.Lock()
_access_token_value = ""
_access_token_expires_at = 0.0


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _service_account_path() -> str:
    return os.getenv("PLAY_INTEGRITY_SERVICE_ACCOUNT_FILE", "").strip()


def is_configured() -> bool:
    path = _service_account_path()
    return bool(path and Path(path).is_file())


def _load_service_account() -> dict[str, Any]:
    path = _service_account_path()
    if not path:
        raise PlayIntegrityError("play_integrity_not_configured")
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise PlayIntegrityError("play_integrity_credentials_invalid") from exc
    if not isinstance(payload, dict):
        raise PlayIntegrityError("play_integrity_credentials_invalid")
    if not str(payload.get("client_email") or "").strip():
        raise PlayIntegrityError("play_integrity_credentials_invalid")
    if not str(payload.get("private_key") or "").strip():
        raise PlayIntegrityError("play_integrity_credentials_invalid")
    return payload


def _service_account_access_token() -> str:
    global _access_token_expires_at, _access_token_value

    now = time.time()
    with _access_token_lock:
        if _access_token_value and now < _access_token_expires_at - 60:
            return _access_token_value

        account = _load_service_account()
        client_email = str(account["client_email"]).strip()
        token_uri = str(account.get("token_uri") or DEFAULT_TOKEN_URI).strip()
        issued_at = int(now)
        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
        claims = _b64url(
            json.dumps(
                {
                    "iss": client_email,
                    "scope": PLAY_INTEGRITY_SCOPE,
                    "aud": token_uri,
                    "iat": issued_at,
                    "exp": issued_at + 3600,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signing_input = f"{header}.{claims}".encode("ascii")
        try:
            private_key = serialization.load_pem_private_key(
                str(account["private_key"]).encode("utf-8"),
                password=None,
            )
            signature = private_key.sign(
                signing_input,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as exc:
            raise PlayIntegrityError("play_integrity_credentials_invalid") from exc

        assertion = f"{header}.{claims}.{_b64url(signature)}"
        try:
            response = httpx.post(
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            token_payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PlayIntegrityError("play_integrity_oauth_failed") from exc

        token = str(token_payload.get("access_token") or "").strip()
        if not token:
            raise PlayIntegrityError("play_integrity_oauth_failed")
        expires_in = max(60, int(token_payload.get("expires_in") or 3600))
        _access_token_value = token
        _access_token_expires_at = now + expires_in
        return token


def _decode_token(integrity_token: str) -> dict[str, Any]:
    token = integrity_token.strip()
    if len(token) < 32 or len(token) > 20000:
        raise PlayIntegrityError("play_integrity_token_invalid")

    package_name = os.getenv("PLAY_INTEGRITY_PACKAGE_NAME", DEFAULT_PACKAGE_NAME).strip() or DEFAULT_PACKAGE_NAME
    access_token = _service_account_access_token()
    url = f"https://playintegrity.googleapis.com/v1/{package_name}:decodeIntegrityToken"
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"integrity_token": token},
            timeout=12.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PlayIntegrityError("play_integrity_decode_failed") from exc

    external = payload.get("tokenPayloadExternal") if isinstance(payload, dict) else None
    if not isinstance(external, dict):
        raise PlayIntegrityError("play_integrity_payload_invalid")
    return external


def verify_standard_token(integrity_token: str, expected_request_hash: str) -> dict[str, Any]:
    expected_hash = expected_request_hash.strip()
    if not expected_hash or len(expected_hash) > 500:
        raise PlayIntegrityError("play_integrity_request_hash_invalid")

    payload = _decode_token(integrity_token)
    package_name = os.getenv("PLAY_INTEGRITY_PACKAGE_NAME", DEFAULT_PACKAGE_NAME).strip() or DEFAULT_PACKAGE_NAME
    max_age_ms = max(
        30_000,
        min(
            int(os.getenv("PLAY_INTEGRITY_MAX_TOKEN_AGE_MILLIS", str(DEFAULT_MAX_TOKEN_AGE_MILLIS))),
            10 * 60 * 1000,
        ),
    )

    request_details = payload.get("requestDetails")
    if not isinstance(request_details, dict):
        raise PlayIntegrityError("play_integrity_request_details_missing")
    if str(request_details.get("requestPackageName") or "") != package_name:
        raise PlayIntegrityError("play_integrity_package_mismatch")
    if str(request_details.get("requestHash") or "") != expected_hash:
        raise PlayIntegrityError("play_integrity_request_hash_mismatch")
    try:
        token_time_ms = int(request_details.get("timestampMillis") or 0)
    except (TypeError, ValueError) as exc:
        raise PlayIntegrityError("play_integrity_timestamp_invalid") from exc
    now_ms = int(time.time() * 1000)
    if token_time_ms <= 0 or abs(now_ms - token_time_ms) > max_age_ms:
        raise PlayIntegrityError("play_integrity_token_stale")

    app_integrity = payload.get("appIntegrity")
    if not isinstance(app_integrity, dict):
        raise PlayIntegrityError("play_integrity_app_missing")
    if str(app_integrity.get("appRecognitionVerdict") or "") != "PLAY_RECOGNIZED":
        raise PlayIntegrityError("play_integrity_app_unrecognized")
    reported_package = str(app_integrity.get("packageName") or "")
    if reported_package and reported_package != package_name:
        raise PlayIntegrityError("play_integrity_package_mismatch")

    expected_certificates = {
        value.strip()
        for value in os.getenv("PLAY_INTEGRITY_CERT_SHA256", "").split(",")
        if value.strip()
    }
    if expected_certificates:
        returned_certificates = {
            str(value).strip()
            for value in (app_integrity.get("certificateSha256Digest") or [])
            if str(value).strip()
        }
        if not returned_certificates.intersection(expected_certificates):
            raise PlayIntegrityError("play_integrity_certificate_mismatch")

    device_integrity = payload.get("deviceIntegrity")
    if not isinstance(device_integrity, dict):
        raise PlayIntegrityError("play_integrity_device_missing")
    verdicts = {
        str(value)
        for value in (device_integrity.get("deviceRecognitionVerdict") or [])
    }
    if "MEETS_DEVICE_INTEGRITY" not in verdicts:
        raise PlayIntegrityError("play_integrity_device_failed")

    return {
        "verified": True,
        "package_name": package_name,
        "device_integrity": sorted(verdicts),
        "app_recognition": "PLAY_RECOGNIZED",
    }
