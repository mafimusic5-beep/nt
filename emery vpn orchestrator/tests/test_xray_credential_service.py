from src.backend.services.xray_credential_service import (
    ScriptOrSshXrayCredentialTransport,
    VlessDeviceConfigBuilder,
)
from src.common.models import VpnAssignment, VpnNode


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
        config_payload=(
            "vless://11111111-1111-4111-8111-111111111111@203.0.113.10:443"
            "?type=tcp&headerType=none&security=reality&fp=chrome&sni=www.cloudflare.com"
            "&pbk=public-key&sid=0123456789abcdef&spx=/#Germany"
        ),
    )


def test_device_link_replaces_only_identity_and_dedicated_port():
    result = VlessDeviceConfigBuilder.build(node(), assignment())

    assert result.startswith(
        "vless://14aec1f1-bf97-47d0-896c-c553a18e2282@203.0.113.10:20007?"
    )
    assert "security=reality" in result
    assert "sni=www.cloudflare.com" in result
    assert result.endswith("#Germany")


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
    assert '"shared_credential_disabled": True' in script
    assert "systemctl\", \"is-active" in script
