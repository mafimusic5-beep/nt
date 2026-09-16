import pytest
from pydantic import ValidationError

from src.backend.schemas.admin import (
    MAX_CONFIGURABLE_NODE_CAPACITY,
    ManualNodeBootstrapRequest,
    VpnNodeUpsertRequest,
)


def test_manual_node_capacity_accepts_exactly_fifteen():
    request = ManualNodeBootstrapRequest(
        region_code="auto",
        endpoint="203.0.113.10",
        ssh_password="test-password",
        capacity_clients=15,
    )
    assert request.capacity_clients == 15


def test_upsert_node_capacity_accepts_exactly_fifteen():
    request = VpnNodeUpsertRequest(
        name="Regional node",
        endpoint="203.0.113.11",
        config_payload="vless://placeholder",
        capacity_clients=15,
    )
    assert request.capacity_clients == 15


def test_node_capacity_defaults_to_fifteen():
    manual = ManualNodeBootstrapRequest(
        region_code="auto",
        endpoint="203.0.113.12",
        ssh_password="test-password",
    )
    upsert = VpnNodeUpsertRequest(
        name="Regional node",
        endpoint="203.0.113.13",
        config_payload="vless://placeholder",
    )
    assert MAX_CONFIGURABLE_NODE_CAPACITY == 15
    assert manual.capacity_clients == 15
    assert upsert.capacity_clients == 15


def test_node_capacity_rejects_sixteenth_client_slot():
    with pytest.raises(ValidationError):
        ManualNodeBootstrapRequest(
            region_code="auto",
            endpoint="203.0.113.14",
            ssh_password="test-password",
            capacity_clients=16,
        )

    with pytest.raises(ValidationError):
        VpnNodeUpsertRequest(
            name="Too large",
            endpoint="203.0.113.15",
            config_payload="vless://placeholder",
            capacity_clients=16,
        )
