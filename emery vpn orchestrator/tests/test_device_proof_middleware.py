from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from src.backend.middleware import device_proof
from src.backend.middleware.device_proof import DeviceProofError, DeviceProofMiddleware


ACCESS_KEY = "SKRYON-TEST-KEY"
HEADERS = {
    "Authorization": f"Bearer {ACCESS_KEY}",
    "X-Emery-Device-Id": "dp1:" + "a" * 64,
    "X-Emery-Timestamp": "1789689600000",
    "X-Emery-Nonce": "0123456789abcdef0123456789abcdef",
    "X-Emery-Signature": "signed-proof-value",
    "X-Emery-Signature-Algorithm": "SHA256withECDSA",
}


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(DeviceProofMiddleware)

    @app.post("/api/v1/vpn/connect")
    async def connect(request: Request):
        return await request.json()

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    return app


def test_vpn_connect_requires_all_keystore_proof_headers(monkeypatch: pytest.MonkeyPatch):
    async def should_not_run(**_kwargs):
        raise AssertionError("authority must not be called for an incomplete proof")

    monkeypatch.setattr(device_proof, "verify_registered_device_proof", should_not_run)
    headers = dict(HEADERS)
    headers.pop("X-Emery-Signature")

    response = TestClient(_app()).post(
        "/api/v1/vpn/connect",
        headers=headers,
        json={"access_key": ACCESS_KEY, "server_id": 1, "traffic_policy": "international"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "device_signature_missing"


def test_vpn_connect_rejects_authorization_body_mismatch(monkeypatch: pytest.MonkeyPatch):
    async def should_not_run(**_kwargs):
        raise AssertionError("authority must not be called for a mismatched access key")

    monkeypatch.setattr(device_proof, "verify_registered_device_proof", should_not_run)

    response = TestClient(_app()).post(
        "/api/v1/vpn/connect",
        headers=HEADERS,
        json={"access_key": "OTHER-KEY", "server_id": 1, "traffic_policy": "international"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "device_proof_mismatch"


def test_vpn_connect_verifies_exact_signed_request_and_preserves_body(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    async def verified(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(device_proof, "verify_registered_device_proof", verified)
    body = {"access_key": ACCESS_KEY, "server_id": 7, "traffic_policy": "international"}

    response = TestClient(_app()).post(
        "/api/v1/vpn/connect",
        headers=HEADERS,
        json=body,
    )

    assert response.status_code == 200
    assert response.json() == body
    assert captured == {
        "access_key": ACCESS_KEY,
        "method": "POST",
        "path": "/api/v1/vpn/connect",
        "device_id": HEADERS["X-Emery-Device-Id"],
        "timestamp": HEADERS["X-Emery-Timestamp"],
        "nonce": HEADERS["X-Emery-Nonce"],
        "signature": HEADERS["X-Emery-Signature"],
        "signature_algorithm": HEADERS["X-Emery-Signature-Algorithm"],
    }


def test_vpn_connect_fails_closed_on_replay(monkeypatch: pytest.MonkeyPatch):
    async def replayed(**_kwargs):
        raise DeviceProofError("device_replay_detected", 401)

    monkeypatch.setattr(device_proof, "verify_registered_device_proof", replayed)

    response = TestClient(_app()).post(
        "/api/v1/vpn/connect",
        headers=HEADERS,
        json={"access_key": ACCESS_KEY, "server_id": 1, "traffic_policy": "international"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "device_replay_detected"


def test_unrelated_routes_do_not_require_device_proof(monkeypatch: pytest.MonkeyPatch):
    async def should_not_run(**_kwargs):
        raise AssertionError("authority must not be called for unrelated routes")

    monkeypatch.setattr(device_proof, "verify_registered_device_proof", should_not_run)

    response = TestClient(_app()).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
