from __future__ import annotations

import importlib.util
import asyncio
import json
import os
import sys
import time
from dataclasses import replace
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


def regional_proof(operation="connect"):
    return dict(proof(), version=2, regional_policy="russia", operation=operation)


def authorized(proof_value):
    return dict(
        allowed=True, target_host="127.0.0.2", target_port=20000, assignment_id=17, node_id=4,
        protocol_version=2, regional_policy="russia", operation=proof_value["operation"],
    )


def ready_state():
    return dict(schema=1, policy="russia", listen_host="127.0.0.2", ports=[20000],
                assignments={"20000": 17}, updated_at=time.time())


def state_config(tmp_path, state=None):
    path = tmp_path / "ready.json"
    path.write_text(json.dumps(state or ready_state()))
    path.chmod(0o644)
    return replace(config(), regional_policy_state_file=str(path))


def test_v2_includes_policy_and_operation_in_authorization():
    payload = regional_proof()
    validated = gate._validated_proof(config(), payload, payload["server_issued_at"], payload["server_nonce"])
    assert validated["protocol_version"] == 2
    assert validated["regional_policy"] == "russia"
    assert validated["operation"] == "connect"
    assert gate._validated_target(config(), authorized(validated), validated) == 20000
    assert gate._target_host(validated) == "127.0.0.2"


@pytest.mark.parametrize("changes", [
    {"version": 1}, {"version": True}, {"version": 3},
    {"regional_policy": "international"}, {"regional_policy": "other"},
    {"operation": "arbitrary"}, {"target_host": "evil.example"},
])
def test_gateway_rejects_ambiguous_or_tampered_v2_proofs(changes):
    payload = dict(regional_proof(), **changes)
    with pytest.raises(gate.GateError):
        gate._validated_proof(config(), payload, payload["server_issued_at"], payload["server_nonce"])


@pytest.mark.parametrize("changes", [
    {"target_host": "127.0.0.1"}, {"regional_policy": "international"},
    {"protocol_version": None}, {"operation": "check"}, {"target_port": True},
])
def test_old_control_plane_or_wrong_policy_target_never_downgrades(changes):
    payload = regional_proof()
    validated = gate._validated_proof(config(), payload, payload["server_issued_at"], payload["server_nonce"])
    with pytest.raises(gate.GateError):
        gate._validated_target(config(), dict(authorized(validated), **changes), validated)


@pytest.mark.parametrize("changes", [
    {"updated_at": time.time() - gate.REGIONAL_POLICY_MAX_AGE - 1},
    {"updated_at": time.time() + 600}, {"updated_at": float("nan")},
    {"ports": []}, {"assignments": {"20000": 16}},
    {"policy": "international"}, {"listen_host": "127.0.0.1"},
])
def test_unready_stale_or_wrong_assignment_is_rejected(tmp_path, changes):
    cfg = state_config(tmp_path, dict(ready_state(), **changes))
    with pytest.raises(gate.GateError):
        gate._regional_policy_deadline(cfg, 20000, 17)


def test_readiness_is_small_root_owned_record(tmp_path):
    cfg = state_config(tmp_path)
    # Tests may run unprivileged in CI; exercise the same validation with a
    # synthetic root owner, not by relaxing the production trust check.
    if os.geteuid() != 0:
        pytest.skip("root ownership fixture requires Linux root")
    assert gate._regional_policy_deadline(cfg, 20000, 17) > time.time()
    Path(cfg.regional_policy_state_file).chmod(0o666)
    with pytest.raises(gate.GateError):
        gate._regional_policy_deadline(cfg, 20000, 17)
    Path(cfg.regional_policy_state_file).unlink()
    with pytest.raises(gate.GateError):
        gate._regional_policy_deadline(cfg, 20000, 17)


class FakeWriter:
    def __init__(self):
        self.messages = []
        self.closed = False

    def write(self, value):
        self.messages.append(value)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def test_regional_preflight_opens_only_filtered_listener_and_closes(monkeypatch):
    targets = []
    writer = FakeWriter()
    target_writer = FakeWriter()

    async def read_proof(reader, timeout):
        challenge = json.loads(writer.messages[0])
        return dict(regional_proof("check"), server_issued_at=challenge["server_issued_at"],
                    server_nonce=challenge["server_nonce"])

    async def connect(host, port):
        targets.append((host, port))
        return asyncio.StreamReader(), target_writer

    monkeypatch.setattr(gate, "_read_json_line", read_proof)
    monkeypatch.setattr(gate, "_authorize_sync", lambda cfg, value: authorized(value))
    monkeypatch.setattr(gate, "_regional_policy_deadline", lambda *args: time.time() + 60)
    monkeypatch.setattr(gate.asyncio, "open_connection", connect)
    asyncio.run(gate.DeviceGate(config()).handle(None, writer))
    assert targets == [("127.0.0.2", 20000)]
    assert json.loads(writer.messages[-1]) == dict(ok=True, protocol_version=2,
                                                  regional_policy="russia", operation="check")
    assert target_writer.closed and writer.closed


def test_unavailable_policy_never_attempts_any_target(monkeypatch):
    writer = FakeWriter()

    async def read_proof(reader, timeout):
        challenge = json.loads(writer.messages[0])
        return dict(regional_proof("check"), server_issued_at=challenge["server_issued_at"],
                    server_nonce=challenge["server_nonce"])

    def unavailable(*args):
        raise gate.GateError("regional policy unavailable")

    async def forbidden(*args):
        pytest.fail("must not fall back to the international listener")

    monkeypatch.setattr(gate, "_read_json_line", read_proof)
    monkeypatch.setattr(gate, "_authorize_sync", lambda cfg, value: authorized(value))
    monkeypatch.setattr(gate, "_regional_policy_deadline", unavailable)
    monkeypatch.setattr(gate.asyncio, "open_connection", forbidden)
    asyncio.run(gate.DeviceGate(config()).handle(None, writer))
    assert json.loads(writer.messages[-1]) == {"ok": False}


def test_international_v1_does_not_require_regional_service(monkeypatch):
    writer = FakeWriter()
    targets = []

    async def read_proof(reader, timeout):
        challenge = json.loads(writer.messages[0])
        assert set(challenge) == {"version", "server_issued_at", "server_nonce"}
        return dict(proof(), server_issued_at=challenge["server_issued_at"], server_nonce=challenge["server_nonce"])

    async def connect(host, port):
        targets.append((host, port))
        return asyncio.StreamReader(), FakeWriter()

    async def pipe(*args):
        pass

    def forbidden(*args):
        pytest.fail("international mode must not consult regional readiness")

    monkeypatch.setattr(gate, "_read_json_line", read_proof)
    monkeypatch.setattr(gate, "_authorize_sync", lambda *args: dict(
        allowed=True, target_host="127.0.0.1", target_port=20000, assignment_id=17, node_id=4))
    monkeypatch.setattr(gate, "_regional_policy_deadline", forbidden)
    monkeypatch.setattr(gate.asyncio, "open_connection", connect)
    monkeypatch.setattr(gate, "_proxy_bidirectional", pipe)
    asyncio.run(gate.DeviceGate(config()).handle(None, writer))
    assert targets == [("127.0.0.1", 20000)]
    assert json.loads(writer.messages[-1]) == {"ok": True}
