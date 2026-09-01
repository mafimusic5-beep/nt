from __future__ import annotations

import base64
import hashlib
import sqlite3
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.requests import Request

import api
import config
import device_auth
import storage


GATE_SPKI_SHA256 = "a" * 64


def _public_key_base64(private_key: ec.EllipticCurvePrivateKey) -> str:
    der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("ascii")


def _sign(private_key: ec.EllipticCurvePrivateKey, canonical: str) -> str:
    value = private_key.sign(canonical.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(value).decode("ascii")


def _activation_canonical(
    code: str,
    device_id: str,
    timestamp: str,
    nonce: str,
) -> str:
    return "\n".join(
        (
            "method=POST",
            "path=/api/activate",
            f"device_id={device_id}",
            "device_name=Test Android",
            f"timestamp={timestamp}",
            f"nonce={nonce}",
            f"auth_sha256={hashlib.sha256(code.encode()).hexdigest()}",
        )
    )


def _gateway_canonical(
    *,
    assignment_id: int,
    node_id: int,
    gate_server_name: str,
    gate_spki_sha256: str,
    device_id: str,
    server_issued_at: str,
    timestamp: str,
    server_nonce: str,
    client_nonce: str,
) -> str:
    return "\n".join(
        (
            "protocol=emery-device-gate-v1",
            f"assignment_id={assignment_id}",
            f"node_id={node_id}",
            f"gate_server_name={gate_server_name}",
            f"gate_spki_sha256={gate_spki_sha256}",
            f"device_id={device_id}",
            f"server_issued_at={server_issued_at}",
            f"timestamp={timestamp}",
            f"server_nonce={server_nonce}",
            f"client_nonce={client_nonce}",
        )
    )


@pytest.fixture()
def registered_assignment(tmp_path, monkeypatch):
    database_path = str(tmp_path / "device-gate.sqlite3")
    monkeypatch.setattr(config, "DATABASE_PATH", database_path)
    monkeypatch.setattr(storage, "DATABASE_PATH", database_path)
    monkeypatch.setattr(device_auth, "DATABASE_PATH", database_path)
    storage.init_storage()
    device_auth.ensure_device_auth_storage()

    code = storage.create_checkout_code(
        "personal",
        1,
        external_id="gate-" + uuid.uuid4().hex,
    )["code"]
    device_id = "registered-device"
    private_key = ec.generate_private_key(ec.SECP256R1())
    timestamp = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex
    device_auth.register_device(
        raw_code=code,
        path="/api/activate",
        device_id=device_id,
        device_name="Test Android",
        public_key_base64=_public_key_base64(private_key),
        timestamp=timestamp,
        nonce=nonce,
        signature_base64=_sign(
            private_key,
            _activation_canonical(code, device_id, timestamp, nonce),
        ),
        signature_algorithm="SHA256withECDSA",
        platform="android",
        app_version="718",
    )
    storage.save_device_pool_assignment(
        code,
        device_id,
        {
            "pool_assignment_id": 17,
            "pool_status": "active",
            "pool_node_id": 4,
            "pool_client_port": 20000,
            "pool_gate_host": "203.0.113.10",
            "pool_gate_port": 24443,
            "pool_gate_server_name": "gate.example.com",
            "pool_gate_spki_sha256": GATE_SPKI_SHA256,
        },
    )
    return {
        "database_path": database_path,
        "code": code,
        "device_id": device_id,
        "private_key": private_key,
    }


def _authorize(registered_assignment, signing_key, *, server_nonce=None, client_nonce=None):
    issued_at = str(int(time.time() * 1000))
    timestamp = issued_at
    resolved_server_nonce = server_nonce or ("server-" + uuid.uuid4().hex)
    resolved_client_nonce = client_nonce or ("client-" + uuid.uuid4().hex)
    canonical = _gateway_canonical(
        assignment_id=17,
        node_id=4,
        gate_server_name="gate.example.com",
        gate_spki_sha256=GATE_SPKI_SHA256,
        device_id=registered_assignment["device_id"],
        server_issued_at=issued_at,
        timestamp=timestamp,
        server_nonce=resolved_server_nonce,
        client_nonce=resolved_client_nonce,
    )
    arguments = {
        "assignment_id": 17,
        "node_id": 4,
        "gate_server_name": "gate.example.com",
        "gate_spki_sha256": GATE_SPKI_SHA256,
        "device_id": registered_assignment["device_id"],
        "server_issued_at": issued_at,
        "timestamp": timestamp,
        "server_nonce": resolved_server_nonce,
        "client_nonce": resolved_client_nonce,
        "signature_base64": _sign(signing_key, canonical),
        "signature_algorithm": "SHA256withECDSA",
    }
    return arguments, device_auth.authorize_gateway_connection(**arguments)


def _regional_arguments(registered_assignment, operation="connect"):
    issued = str(int(time.time() * 1000))
    fields = dict(
        assignment_id=17, node_id=4, gate_server_name="gate.example.com",
        gate_spki_sha256=GATE_SPKI_SHA256, device_id=registered_assignment["device_id"],
        server_issued_at=issued, timestamp=issued,
        server_nonce="server-" + uuid.uuid4().hex, client_nonce="client-" + uuid.uuid4().hex,
        protocol_version=2, regional_policy="russia", operation=operation,
    )
    # Independent wire canonical, not the production builder under test.
    canonical = _gateway_canonical(**{key: value for key, value in fields.items()
                                    if key not in ("protocol_version", "regional_policy", "operation")})
    canonical = canonical.replace("protocol=emery-device-gate-v1", "protocol=emery-device-gate-v2")
    canonical += f"\nregional_policy=russia\noperation={operation}"
    assert device_auth._gateway_canonical(**fields) == canonical
    return dict(fields, signature_base64=_sign(registered_assignment["private_key"], canonical),
                signature_algorithm="SHA256withECDSA")


@pytest.mark.parametrize("operation", ["connect", "check"])
def test_signed_regional_mode_selects_only_restricted_loopback(registered_assignment, operation):
    result = device_auth.authorize_gateway_connection(**_regional_arguments(registered_assignment, operation))
    assert result["target_host"] == "127.0.0.2"
    assert result["target_port"] == 20000
    assert result["regional_policy"] == "russia"
    assert result["protocol_version"] == 2
    assert result["operation"] == operation


@pytest.mark.parametrize("changes", [
    {"operation": "check"},
    {"regional_policy": "international"},
    {"protocol_version": 1, "regional_policy": "international"},
    {"protocol_version": 99},
    {"regional_policy": "other"},
])
def test_regional_signature_cannot_be_repurposed(registered_assignment, changes):
    arguments = _regional_arguments(registered_assignment)
    arguments.update(changes)
    with pytest.raises(device_auth.DeviceAuthError):
        device_auth.authorize_gateway_connection(**arguments)


def test_regional_proof_is_single_use(registered_assignment):
    arguments = _regional_arguments(registered_assignment)
    device_auth.authorize_gateway_connection(**arguments)
    with pytest.raises(device_auth.DeviceAuthError):
        device_auth.authorize_gateway_connection(**arguments)


def test_internal_api_passes_signed_regional_fields(registered_assignment, monkeypatch):
    monkeypatch.setattr(api, "DEVICE_GATE_API_KEY", "k" * 32)
    arguments = _regional_arguments(registered_assignment, "check")
    arguments["signature"] = arguments.pop("signature_base64")
    payload = api.DeviceGateAuthorizeRequest(**arguments)
    request = Request({
        "type": "http", "method": "POST", "path": "/internal/device-gate/authorize",
        "headers": [(b"x-device-gate-key", b"k" * 32)],
    })
    result = api.device_gate_authorize(payload, request)
    assert result["target_host"] == "127.0.0.2"
    assert result["regional_policy"] == "russia"
    assert result["operation"] == "check"


def test_registered_device_key_authorizes_only_loopback_target(registered_assignment):
    _, result = _authorize(registered_assignment, registered_assignment["private_key"])

    assert result == {
        "allowed": True,
        "target_host": "127.0.0.1",
        "target_port": 20000,
        "assignment_id": 17,
        "node_id": 4,
    }


def test_copied_vless_metadata_is_useless_with_attacker_key(registered_assignment):
    attacker_key = ec.generate_private_key(ec.SECP256R1())

    with pytest.raises(device_auth.DeviceAuthError) as error:
        _authorize(registered_assignment, attacker_key)

    assert error.value.reason == "device_signature_invalid"


def test_device_gate_proof_cannot_be_replayed(registered_assignment):
    arguments, _ = _authorize(registered_assignment, registered_assignment["private_key"])

    with pytest.raises(device_auth.DeviceAuthError) as error:
        device_auth.authorize_gateway_connection(**arguments)

    assert error.value.reason == "device_replay_detected"


def test_deactivated_device_is_denied_even_with_valid_key(registered_assignment):
    with sqlite3.connect(registered_assignment["database_path"]) as connection:
        connection.execute(
            "UPDATE code_devices SET active = 0 WHERE device_id = ?",
            (registered_assignment["device_id"],),
        )
        connection.commit()

    with pytest.raises(device_auth.DeviceAuthError) as error:
        _authorize(registered_assignment, registered_assignment["private_key"])

    assert error.value.reason == "device_gate_not_authorized"


def test_proof_is_bound_to_gateway_tls_name(registered_assignment):
    issued_at = str(int(time.time() * 1000))
    server_nonce = "server-" + uuid.uuid4().hex
    client_nonce = "client-" + uuid.uuid4().hex
    canonical = _gateway_canonical(
        assignment_id=17,
        node_id=4,
        gate_server_name="attacker.example.com",
        gate_spki_sha256=GATE_SPKI_SHA256,
        device_id=registered_assignment["device_id"],
        server_issued_at=issued_at,
        timestamp=issued_at,
        server_nonce=server_nonce,
        client_nonce=client_nonce,
    )

    with pytest.raises(device_auth.DeviceAuthError) as error:
        device_auth.authorize_gateway_connection(
            assignment_id=17,
            node_id=4,
            gate_server_name="attacker.example.com",
            gate_spki_sha256=GATE_SPKI_SHA256,
            device_id=registered_assignment["device_id"],
            server_issued_at=issued_at,
            timestamp=issued_at,
            server_nonce=server_nonce,
            client_nonce=client_nonce,
            signature_base64=_sign(registered_assignment["private_key"], canonical),
            signature_algorithm="SHA256withECDSA",
        )

    assert error.value.reason == "device_gate_not_authorized"


def test_proof_is_bound_to_gateway_certificate_pin(registered_assignment):
    issued_at = str(int(time.time() * 1000))
    server_nonce = "server-" + uuid.uuid4().hex
    client_nonce = "client-" + uuid.uuid4().hex
    attacker_pin = "b" * 64
    canonical = _gateway_canonical(
        assignment_id=17,
        node_id=4,
        gate_server_name="gate.example.com",
        gate_spki_sha256=attacker_pin,
        device_id=registered_assignment["device_id"],
        server_issued_at=issued_at,
        timestamp=issued_at,
        server_nonce=server_nonce,
        client_nonce=client_nonce,
    )

    with pytest.raises(device_auth.DeviceAuthError) as error:
        device_auth.authorize_gateway_connection(
            assignment_id=17,
            node_id=4,
            gate_server_name="gate.example.com",
            gate_spki_sha256=attacker_pin,
            device_id=registered_assignment["device_id"],
            server_issued_at=issued_at,
            timestamp=issued_at,
            server_nonce=server_nonce,
            client_nonce=client_nonce,
            signature_base64=_sign(registered_assignment["private_key"], canonical),
            signature_algorithm="SHA256withECDSA",
        )

    assert error.value.reason == "device_gate_not_authorized"


def test_internal_authorize_endpoint_requires_separate_gate_key(monkeypatch):
    monkeypatch.setattr(api, "DEVICE_GATE_API_KEY", "expected-gate-secret")
    payload = api.DeviceGateAuthorizeRequest(
        assignment_id=17,
        node_id=4,
        gate_server_name="gate.example.com",
        gate_spki_sha256=GATE_SPKI_SHA256,
        device_id="registered-device",
        server_issued_at="1787500000000",
        timestamp="1787500000001",
        server_nonce="server-0123456789abcdef",
        client_nonce="client-0123456789abcdef",
        signature="signed-value-0123456789",
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/device-gate/authorize",
            "headers": [(b"x-device-gate-key", b"wrong-secret")],
        }
    )

    response = api.device_gate_authorize(payload, request)

    assert response.status_code == 403
