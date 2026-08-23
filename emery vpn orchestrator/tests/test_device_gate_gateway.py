from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


GATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "device-gate"
    / "emery_device_gate.py"
)
SPEC = importlib.util.spec_from_file_location("emery_device_gate", GATE_PATH)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def config():
    return gate.Config(
        bind_host="127.0.0.1",
        bind_port=24443,
        node_id=4,
        server_name="gate.example.com",
        spki_sha256="a" * 64,
        tls_cert_file="cert.pem",
        tls_key_file="key.pem",
        authorize_url="https://activation.example.com/internal/device-gate/authorize",
        authorize_key="secret",
        control_timeout_seconds=10,
        connect_timeout_seconds=5,
        max_connections=100,
    )


def proof():
    return {
        "version": 1,
        "assignment_id": 17,
        "node_id": 4,
        "gate_server_name": "gate.example.com",
        "gate_spki_sha256": "a" * 64,
        "device_id": "registered-device",
        "server_issued_at": "1787500000000",
        "timestamp": "1787500000001",
        "server_nonce": "server-0123456789abcdef",
        "client_nonce": "client-0123456789abcdef",
        "signature": "signed-value",
        "signature_algorithm": "SHA256withECDSA",
    }


def test_gateway_accepts_only_its_own_challenge_and_tls_identity():
    payload = proof()

    validated = gate._validated_proof(
        config(),
        payload,
        payload["server_issued_at"],
        payload["server_nonce"],
    )

    assert "version" not in validated
    assert validated["gate_server_name"] == "gate.example.com"
    assert validated["gate_spki_sha256"] == "a" * 64

    changed = proof()
    changed["gate_server_name"] = "attacker.example.com"
    with pytest.raises(gate.GateError):
        gate._validated_proof(
            config(),
            changed,
            changed["server_issued_at"],
            changed["server_nonce"],
        )

    changed = proof()
    changed["gate_spki_sha256"] = "b" * 64
    with pytest.raises(gate.GateError):
        gate._validated_proof(
            config(),
            changed,
            changed["server_issued_at"],
            changed["server_nonce"],
        )


def test_gateway_never_accepts_non_loopback_authorization_target():
    validated = gate._validated_proof(
        config(), proof(), proof()["server_issued_at"], proof()["server_nonce"]
    )
    result = {
        "allowed": True,
        "target_host": "203.0.113.10",
        "target_port": 20000,
        "assignment_id": 17,
        "node_id": 4,
    }

    with pytest.raises(gate.GateError):
        gate._validated_target(config(), result, validated)


def test_remote_plain_http_authorization_is_rejected(monkeypatch):
    values = {
        "EMERY_GATE_NODE_ID": "4",
        "EMERY_GATE_SERVER_NAME": "gate.example.com",
        "EMERY_GATE_SPKI_SHA256": "a" * 64,
        "EMERY_GATE_TLS_CERT_FILE": "cert.pem",
        "EMERY_GATE_TLS_KEY_FILE": "key.pem",
        "EMERY_GATE_AUTHORIZE_URL": (
            "http://activation.example.com/internal/device-gate/authorize"
        ),
        "EMERY_GATE_AUTHORIZE_KEY": "secret",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(gate.GateError):
        gate.Config.from_env()
