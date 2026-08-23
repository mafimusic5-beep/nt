from src.backend.services.xray_credential_service import (
    ScriptOrSshXrayCredentialTransport,
    VlessDeviceConfigBuilder,
)
from src.common.models import VpnAssignment, VpnNode
from src.common.config import settings


GATE_SPKI_SHA256 = "a" * 64


def assignment() -> VpnAssignment:
    return VpnAssignment(
        id=9,
        subject_type="legacy_device",
        subject_key="a" * 64,
        entitlement_hash="b" * 64,
        node_id=3,
        client_uuid="14aec1f1-bf97-47d0-896c-c553a18e2282",
        client_port=20007,
        speed_limit_mbps=30,
    )


def node() -> VpnNode:
    return VpnNode(
        id=3,
        region_code="de",
        name="Germany",
        endpoint="203.0.113.10",
        device_gate_host="203.0.113.10",
        device_gate_port=24443,
        device_gate_server_name="gate.example.com",
        device_gate_spki_sha256=GATE_SPKI_SHA256,
        config_payload=(
            "vless://11111111-1111-4111-8111-111111111111@203.0.113.10:443"
            "?type=tcp&headerType=none&security=reality&fp=chrome&sni=www.cloudflare.com"
            "&pbk=public-key&sid=0123456789abcdef&spx=/#Germany"
        ),
    )


def test_device_link_requires_local_proof_proxy(monkeypatch):
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)
    result = VlessDeviceConfigBuilder.build(node(), assignment())

    assert result.startswith(
        "vless://14aec1f1-bf97-47d0-896c-c553a18e2282@127.0.0.1:17890?"
    )
    assert "security=reality" in result
    assert "sni=www.cloudflare.com" in result
    assert "eg_host=203.0.113.10" in result
    assert "eg_port=24443" in result
    assert "eg_sni=gate.example.com" in result
    assert f"eg_spki={GATE_SPKI_SHA256}" in result
    assert "eg_assignment=9" in result
    assert "eg_node=3" in result
    assert result.endswith("#Germany")


def test_device_link_is_withheld_when_gate_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "device_bound_gate_enabled", False)

    assert VlessDeviceConfigBuilder.build(node(), assignment()) == ""


def test_remote_mutation_is_valid_python_and_contains_mandatory_controls():
    script = ScriptOrSshXrayCredentialTransport._remote_script("upsert_client", assignment())

    compile(script, "remote-xray-credential.py", "exec")
    assert "fcntl.LOCK_EX" in script
    assert ".emery-xray-credentials.lock" in script
    assert "speed * 1000 // 8" in script
    assert "emery_vpn_rate_check" in script
    assert '"25,465,587"' in script
    assert '"geoip:private"' in script
    assert 'base.setdefault("settings", {})["clients"] = []' in script
    assert 'dedicated["listen"] = "127.0.0.1"' in script
    assert '"shared_credential_disabled": True' in script
    assert '"direct_ingress_blocked": True' in script
    assert '"device_gate_ready": True' in script
    assert '"emery-device-gate"' in script
    assert "systemctl\", \"is-active" in script
