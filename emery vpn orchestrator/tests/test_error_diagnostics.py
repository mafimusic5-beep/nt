from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.backend.api.error_diagnostics import diagnose_capacity_failure_in_session
from src.common.config import settings
from src.common.models import VpnAssignment, VpnNode


@pytest.fixture(autouse=True)
def diagnostic_settings(monkeypatch):
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)
    monkeypatch.setattr(settings, "device_gate_client_loopback_port", 17890)
    monkeypatch.setattr(settings, "xray_client_port_start", 20000)
    monkeypatch.setattr(settings, "xray_client_port_end", 20019)


def add_node(
    db_session,
    *,
    region: str = "de",
    status: str = "active",
    health: str = "healthy",
    current_clients: int = 0,
    capacity_clients: int = 20,
    gate_ready: bool = True,
) -> VpnNode:
    node = VpnNode(
        region_code=region,
        name="Diagnostic node",
        provider="manual",
        status=status,
        endpoint="203.0.113.10",
        config_payload="vless://11111111-1111-4111-8111-111111111111@203.0.113.10:443",
        health_status=health,
        capacity_clients=capacity_clients,
        current_clients=current_clients,
        bandwidth_limit_mbps=600,
        per_device_speed_limit_mbps=30,
        device_gate_host="gate.example.com" if gate_ready else "",
        device_gate_port=24443,
        device_gate_server_name="gate.example.com" if gate_ready else "",
        device_gate_spki_sha256="a" * 64 if gate_ready else "",
    )
    db_session.add(node)
    db_session.commit()
    return node


def test_no_nodes_is_reported(db_session):
    assert diagnose_capacity_failure_in_session(db_session, "auto") == "pool_no_nodes"
    assert diagnose_capacity_failure_in_session(db_session, "de") == "pool_region_unavailable"


def test_no_active_nodes_is_reported(db_session):
    add_node(db_session, status="maintenance", health="down")
    assert diagnose_capacity_failure_in_session(db_session, "auto") == "pool_no_active_nodes"


def test_no_healthy_nodes_is_reported(db_session):
    add_node(db_session, status="active", health="down")
    assert diagnose_capacity_failure_in_session(db_session, "auto") == "pool_no_healthy_nodes"


def test_capacity_full_is_reported(db_session):
    add_node(db_session, current_clients=20, capacity_clients=20)
    assert diagnose_capacity_failure_in_session(db_session, "auto") == "pool_capacity_full"


def test_gate_not_ready_is_reported(db_session):
    add_node(db_session, gate_ready=False)
    assert diagnose_capacity_failure_in_session(db_session, "auto") == "pool_gate_not_ready"


def test_ports_exhausted_is_reported(db_session, monkeypatch):
    monkeypatch.setattr(settings, "xray_client_port_start", 20000)
    monkeypatch.setattr(settings, "xray_client_port_end", 20000)
    node = add_node(db_session, current_clients=0, capacity_clients=20)
    assignment = VpnAssignment(
        subject_type="legacy_device",
        subject_key="a" * 64,
        entitlement_hash="b" * 64,
        entitlement_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        node_id=node.id,
        client_uuid="11111111-1111-4111-8111-111111111112",
        client_port=20000,
        speed_limit_mbps=30,
        status="active",
        device_gate_enforced=True,
    )
    db_session.add(assignment)
    db_session.commit()

    assert diagnose_capacity_failure_in_session(db_session, "auto") == "pool_ports_exhausted"


def test_remaining_case_is_retryable_race(db_session):
    add_node(db_session)
    assert diagnose_capacity_failure_in_session(db_session, "auto") == "pool_assignment_race"
