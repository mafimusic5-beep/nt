from fastapi import HTTPException
import pytest

from src.backend.schemas.admin import (
    ManualNodeBootstrapRequest,
    VpnNodeDeviceGateRequest,
    VpnNodeUpsertRequest,
)
from src.backend.services.admin_service import AdminService
from src.backend.services.manual_device_gate_service import ManualDeviceGateService
from src.backend.services.manual_node_admin_service import ManualNodeAdminService
from src.backend.services.manual_node_bootstrap_service import ManualNodeBootstrapService
from src.common.config import settings


VALID_CONFIG = (
    "vless://00000000-0000-0000-0000-000000000000@203.0.113.10:443"
    "?encryption=none&flow=xtls-rprx-vision&security=reality&sni=example.com"
    "&fp=chrome&pbk=public-key&sid=abcd&type=tcp#Germany"
)


def _node_request(**overrides) -> VpnNodeUpsertRequest:
    values = {
        "name": "Existing node",
        "region_code": "de",
        "endpoint": "203.0.113.10",
        "config_payload": VALID_CONFIG,
        "status": "active",
        "health_status": "healthy",
    }
    values.update(overrides)
    return VpnNodeUpsertRequest(**values)


def test_existing_node_can_be_configured_before_gate_enable(db_session, monkeypatch):
    monkeypatch.setattr(settings, "device_bound_gate_enabled", False)
    service = AdminService(db_session)
    node = service.create_node(_node_request())

    configured = service.configure_node_device_gate(
        node.id,
        VpnNodeDeviceGateRequest(
            device_gate_host="GATE.EXAMPLE.COM",
            device_gate_port=24443,
            device_gate_server_name="GATE.EXAMPLE.COM",
            device_gate_spki_sha256="A" * 64,
        ),
    )

    assert configured.device_gate_host == "gate.example.com"
    assert configured.device_gate_server_name == "gate.example.com"
    assert configured.device_gate_spki_sha256 == "a" * 64


def test_partial_or_invalid_gate_configuration_is_rejected(db_session, monkeypatch):
    monkeypatch.setattr(settings, "device_bound_gate_enabled", False)
    service = AdminService(db_session)

    with pytest.raises(HTTPException) as partial:
        service.create_node(_node_request(device_gate_host="gate.example.com"))
    assert partial.value.detail == "device_gate_endpoint_required"

    node = service.create_node(_node_request())
    with pytest.raises(HTTPException) as invalid_pin:
        service.configure_node_device_gate(
            node.id,
            VpnNodeDeviceGateRequest(
                device_gate_host="gate.example.com",
                device_gate_port=24443,
                device_gate_server_name="gate.example.com",
                device_gate_spki_sha256="not-a-pin",
            ),
        )
    assert invalid_pin.value.detail == "device_gate_spki_invalid"


def test_gate_enabled_requires_complete_node_configuration(db_session, monkeypatch):
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)

    with pytest.raises(HTTPException) as error:
        AdminService(db_session).create_node(_node_request())

    assert error.value.detail == "device_gate_endpoint_required"


def test_rebootstrap_reuses_existing_gate_when_short_command_omits_it(db_session, monkeypatch):
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)
    service = AdminService(db_session)
    existing = service.create_node(
        _node_request(
            device_gate_host="gate.example.com",
            device_gate_port=8447,
            device_gate_server_name="gate.example.com",
            device_gate_spki_sha256="a" * 64,
        )
    )

    monkeypatch.setattr(
        ManualNodeBootstrapService,
        "bootstrap_with_password",
        lambda self, node, *, ssh_user, ssh_password: {
            "status": "ok",
            "isp_egress_enabled": False,
        },
    )
    monkeypatch.setattr(
        ManualDeviceGateService,
        "bootstrap",
        lambda self, node: {
            "status": "ok",
            "host": "gate.example.com",
            "port": 8447,
            "server_name": "gate.example.com",
            "spki_sha256": "a" * 64,
        },
    )

    result = ManualNodeAdminService(db_session).bootstrap(
        ManualNodeBootstrapRequest(
            name="Server",
            region_code="de",
            endpoint="203.0.113.10",
            ssh_password="temporary-password",
        )
    )

    assert result.node.id == existing.id
    assert result.node.device_gate_host == "gate.example.com"
    assert result.node.device_gate_port == 8447
    assert result.node.device_gate_server_name == "gate.example.com"
    assert result.node.device_gate_spki_sha256 == "a" * 64
