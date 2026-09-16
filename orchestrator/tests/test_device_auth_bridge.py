from __future__ import annotations

import json

import pytest

import checkout_routes
from checkout_routes import DeviceAuthVerifyRequest
from device_auth import DeviceAuthError


PAYLOAD = DeviceAuthVerifyRequest(
    access_key="SKRYON-TEST-KEY",
    method="POST",
    path="/api/v1/vpn/connect",
    device_id="dp1:" + "a" * 64,
    timestamp="1789689600000",
    nonce="0123456789abcdef0123456789abcdef",
    signature="signed-proof-value",
    signature_algorithm="SHA256withECDSA",
)


def _json_response_payload(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_internal_device_auth_bridge_requires_shared_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(checkout_routes, "POOL_BRIDGE_API_KEY", "shared-secret")

    def should_not_run(**_kwargs):
        raise AssertionError("device authority must not run without bridge authentication")

    monkeypatch.setattr(checkout_routes, "authenticate_registered_device", should_not_run)

    response = checkout_routes.verify_device_auth(PAYLOAD, x_pool_bridge_key="wrong-secret")

    assert response.status_code == 403
    assert _json_response_payload(response)["reason"] == "device_auth_forbidden"


def test_internal_device_auth_bridge_verifies_original_connect_request(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(checkout_routes, "POOL_BRIDGE_API_KEY", "shared-secret")
    captured = {}

    def verified(**kwargs):
        captured.update(kwargs)
        return {"device_id": PAYLOAD.device_id}

    monkeypatch.setattr(checkout_routes, "authenticate_registered_device", verified)

    response = checkout_routes.verify_device_auth(PAYLOAD, x_pool_bridge_key="shared-secret")

    assert response == {"allowed": True, "device_id": PAYLOAD.device_id}
    assert captured == {
        "raw_code": PAYLOAD.access_key,
        "method": "POST",
        "path": "/api/v1/vpn/connect",
        "device_id": PAYLOAD.device_id,
        "timestamp": PAYLOAD.timestamp,
        "nonce": PAYLOAD.nonce,
        "signature_base64": PAYLOAD.signature,
        "signature_algorithm": PAYLOAD.signature_algorithm,
    }


def test_internal_device_auth_bridge_returns_replay_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(checkout_routes, "POOL_BRIDGE_API_KEY", "shared-secret")

    def replayed(**_kwargs):
        raise DeviceAuthError("device_replay_detected", 401)

    monkeypatch.setattr(checkout_routes, "authenticate_registered_device", replayed)

    response = checkout_routes.verify_device_auth(PAYLOAD, x_pool_bridge_key="shared-secret")

    assert response.status_code == 401
    assert _json_response_payload(response) == {
        "allowed": False,
        "reason": "device_replay_detected",
    }


def test_internal_device_auth_bridge_rejects_non_path_input(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(checkout_routes, "POOL_BRIDGE_API_KEY", "shared-secret")
    invalid = PAYLOAD.model_copy(update={"path": "api/v1/vpn/connect"})

    response = checkout_routes.verify_device_auth(invalid, x_pool_bridge_key="shared-secret")

    assert response.status_code == 400
    assert _json_response_payload(response)["reason"] == "device_proof_invalid"
