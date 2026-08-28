from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from src.backend.services.xray_credential_service import VlessDeviceConfigBuilder
from src.common.config import settings


def _node(config_payload: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=2,
        region_code="DE",
        config_payload=config_payload,
        device_gate_host="gate.example.com",
        device_gate_port=8447,
        device_gate_server_name="gate.example.com",
        device_gate_spki_sha256="a" * 64,
    )


def _assignment() -> SimpleNamespace:
    return SimpleNamespace(
        id=17,
        client_uuid="11111111-2222-3333-4444-555555555555",
    )


def test_reality_gate_uri_defaults_missing_flow_to_vision(monkeypatch) -> None:
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)
    monkeypatch.setattr(settings, "device_gate_client_loopback_port", 29000)
    node = _node(
        "vless://legacy@198.51.100.10:443?type=tcp&security=reality"
        "&sni=www.cloudflare.com&pbk=public-key&sid=abcd#Germany"
    )

    uri = VlessDeviceConfigBuilder.build(node, _assignment())
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query, keep_blank_values=True)

    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == 29000
    assert query["flow"] == ["xtls-rprx-vision"]
    assert query["security"] == ["reality"]
    assert query["eg_assignment"] == ["17"]
    assert query["eg_node"] == ["2"]


def test_reality_gate_uri_preserves_explicit_flow(monkeypatch) -> None:
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)
    monkeypatch.setattr(settings, "device_gate_client_loopback_port", 29000)
    node = _node(
        "vless://legacy@198.51.100.10:443?type=tcp&security=reality"
        "&flow=explicit-flow&sni=www.cloudflare.com&pbk=public-key&sid=abcd"
    )

    uri = VlessDeviceConfigBuilder.build(node, _assignment())
    query = parse_qs(urlsplit(uri).query, keep_blank_values=True)

    assert query["flow"] == ["explicit-flow"]
