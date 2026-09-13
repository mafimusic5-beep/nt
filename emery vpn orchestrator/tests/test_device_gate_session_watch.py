from __future__ import annotations

import asyncio
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
SPEC = importlib.util.spec_from_file_location("emery_device_gate_session_watch", GATE_PATH)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def _config():
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
        session_check_interval_seconds=1,
    )


def _proof():
    return {
        "assignment_id": 17,
        "node_id": 4,
        "gate_server_name": "gate.example.com",
        "gate_spki_sha256": "a" * 64,
        "device_id": "registered-device",
    }


def test_session_check_uses_internal_sibling_route():
    assert (
        gate._session_check_url(_config())
        == "https://activation.example.com/internal/device-gate/session-check"
    )


def test_authorization_requires_server_credential_epoch():
    epoch = "b" * 64
    assert gate._validated_credential_epoch({"credential_epoch": epoch}) == epoch

    with pytest.raises(gate.GateError):
        gate._validated_credential_epoch({})

    with pytest.raises(gate.GateError):
        gate._validated_credential_epoch({"credential_epoch": "bad"})


def test_existing_tunnel_survives_unavailable_checks_until_explicit_revoke(monkeypatch):
    decisions = iter((None, None, True, None, False))
    calls = []

    def fake_check(config, proof, epoch):
        calls.append((proof["assignment_id"], epoch))
        return next(decisions)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(gate, "_session_check_sync", fake_check)
    monkeypatch.setattr(gate.asyncio, "sleep", no_sleep)

    asyncio.run(gate._watch_session(_config(), _proof(), "c" * 64))

    assert len(calls) == 5
    assert all(item == (17, "c" * 64) for item in calls)
