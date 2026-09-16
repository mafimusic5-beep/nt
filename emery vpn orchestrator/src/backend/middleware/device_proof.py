from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from src.common.config import settings


VPN_CONNECT_PATH = "/api/v1/vpn/connect"
DEVICE_AUTH_VERIFY_PATH = "/internal/device-auth/verify"
DEVICE_AUTH_AUTHORITY_URL = os.getenv(
    "DEVICE_AUTH_AUTHORITY_URL",
    "http://127.0.0.1:8080",
).strip().rstrip("/")
DEVICE_AUTH_TIMEOUT_SECONDS = 5.0


@dataclass
class DeviceProofError(Exception):
    reason: str
    status_code: int = 401

    def __str__(self) -> str:
        return self.reason


def _error_response(error: DeviceProofError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.reason, "error": error.reason},
        headers={"Cache-Control": "no-store"},
    )


def _required_header(request: Request, name: str, *, max_length: int) -> str:
    value = request.headers.get(name, "").strip()
    if not value or len(value) > max_length or "\n" in value or "\r" in value:
        raise DeviceProofError("device_signature_missing", 401)
    return value


def _bearer_access_key(request: Request) -> str:
    authorization = request.headers.get("authorization", "").strip()
    if not authorization.lower().startswith("bearer "):
        raise DeviceProofError("invalid_or_expired_key", 401)
    access_key = authorization[7:].strip()
    if not access_key or len(access_key) > 64:
        raise DeviceProofError("invalid_or_expired_key", 401)
    return access_key


def _body_access_key(raw_body: bytes) -> str:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeviceProofError("bad_request", 400) from exc
    if not isinstance(payload, dict):
        raise DeviceProofError("bad_request", 400)
    access_key = str(payload.get("access_key") or "").strip()
    if not access_key or len(access_key) > 64:
        raise DeviceProofError("bad_request", 400)
    return access_key


def _authority_reason(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return f"device_auth_http_{response.status_code}"
    if not isinstance(payload, dict):
        return f"device_auth_http_{response.status_code}"
    return str(
        payload.get("reason")
        or payload.get("error")
        or payload.get("detail")
        or f"device_auth_http_{response.status_code}"
    )


async def verify_registered_device_proof(
    *,
    access_key: str,
    method: str,
    path: str,
    device_id: str,
    timestamp: str,
    nonce: str,
    signature: str,
    signature_algorithm: str,
) -> None:
    """Ask the legacy device authority to verify the Android Keystore signature.

    The legacy authority owns the registered public key and nonce replay table.
    The modern backend deliberately does not keep a second copy of that state.
    """
    bridge_key = settings.pool_bridge_api_key.strip()
    if not bridge_key or not DEVICE_AUTH_AUTHORITY_URL:
        raise DeviceProofError("device_auth_unavailable", 503)

    try:
        async with httpx.AsyncClient(timeout=DEVICE_AUTH_TIMEOUT_SECONDS) as client:
            response = await client.post(
                DEVICE_AUTH_AUTHORITY_URL + DEVICE_AUTH_VERIFY_PATH,
                headers={"X-Pool-Bridge-Key": bridge_key},
                json={
                    "access_key": access_key,
                    "method": method,
                    "path": path,
                    "device_id": device_id,
                    "timestamp": timestamp,
                    "nonce": nonce,
                    "signature": signature,
                    "signature_algorithm": signature_algorithm,
                },
            )
    except httpx.HTTPError as exc:
        raise DeviceProofError("device_auth_unavailable", 503) from exc

    if response.status_code >= 400:
        reason = _authority_reason(response)
        if response.status_code in {400, 401, 403, 409}:
            raise DeviceProofError(reason, response.status_code)
        raise DeviceProofError("device_auth_unavailable", 503)

    try:
        payload = response.json()
    except ValueError as exc:
        raise DeviceProofError("device_auth_invalid_response", 503) from exc
    if not isinstance(payload, dict) or payload.get("allowed") is not True:
        raise DeviceProofError("device_not_authorized", 403)
    confirmed_device_id = str(payload.get("device_id") or "").strip()
    if not confirmed_device_id or not hmac.compare_digest(confirmed_device_id, device_id):
        raise DeviceProofError("device_mismatch", 403)


class DeviceProofMiddleware(BaseHTTPMiddleware):
    """Fail closed on VPN-connect requests that are not signed by the bound device key."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method.upper() != "POST" or request.url.path != VPN_CONNECT_PATH:
            return await call_next(request)

        try:
            raw_body = await request.body()
            body_access_key = _body_access_key(raw_body)
            bearer_access_key = _bearer_access_key(request)
            if not hmac.compare_digest(body_access_key, bearer_access_key):
                raise DeviceProofError("device_proof_mismatch", 401)

            device_id = _required_header(request, "X-Emery-Device-Id", max_length=128)
            timestamp = _required_header(request, "X-Emery-Timestamp", max_length=32)
            nonce = _required_header(request, "X-Emery-Nonce", max_length=128)
            signature = _required_header(request, "X-Emery-Signature", max_length=2048)
            signature_algorithm = _required_header(
                request,
                "X-Emery-Signature-Algorithm",
                max_length=64,
            )

            await verify_registered_device_proof(
                access_key=body_access_key,
                method="POST",
                path=VPN_CONNECT_PATH,
                device_id=device_id,
                timestamp=timestamp,
                nonce=nonce,
                signature=signature,
                signature_algorithm=signature_algorithm,
            )
        except DeviceProofError as error:
            return _error_response(error)

        return await call_next(request)
