import pytest
from pydantic import ValidationError

from src.backend.schemas.admin import (
    MAX_CONFIGURABLE_NODE_CAPACITY,
    ManualNodeBootstrapRequest,
    VpnNodeUpsertRequest,
)


def test_manual_node_capacity_is_not_tied_to_small_vps_profile():
    request = ManualNodeBootstrapRequest(
        region_code="auto",
        endpoint="203.0.113.10",
        ssh_password="test-password",
        capacity_clients=5_000,
    )
    assert request.capacity_clients == 5_000


def test_upsert_node_capacity_supports_vertical_scaling():
    request = VpnNodeUpsertRequest(
        name="Scaled node",
        endpoint="203.0.113.11",
        config_payload="vless://placeholder",
        capacity_clients=5_000,
    )
    assert request.capacity_clients == 5_000


def test_node_capacity_keeps_a_safety_ceiling():
    with pytest.raises(ValidationError):
        ManualNodeBootstrapRequest(
            region_code="auto",
            endpoint="203.0.113.12",
            ssh_password="test-password",
            capacity_clients=MAX_CONFIGURABLE_NODE_CAPACITY + 1,
        )
