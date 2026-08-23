from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from src.backend.schemas.pool_bridge import (
    PoolReservationConfirmRequest,
    PoolReservationPrepareRequest,
)
from src.backend.services.pool_assignment_service import PoolAssignmentService
from src.backend.services.xray_credential_service import CredentialMutationResult
from src.common.config import settings
from src.common.models import VpnAssignment, VpnNode


BASE_UUID = "11111111-1111-4111-8111-111111111111"
GATE_SPKI_SHA256 = "a" * 64
BASE_CONFIG = (
    f"vless://{BASE_UUID}@203.0.113.10:443?type=tcp&headerType=none&security=reality"
    "&fp=chrome&sni=www.cloudflare.com&pbk=test-public-key"
    "&sid=0123456789abcdef&spx=/#Germany"
)


class FakeCredentialTransport:
    def __init__(self, *, safe: bool = True) -> None:
        self.safe = safe
        self.installed: list[int] = []
        self.removed: list[int] = []

    def install(self, node, assignment):
        self.installed.append(assignment.id)
        return CredentialMutationResult(
            True,
            "installed",
            rate_limit_enforced=self.safe,
            smtp_block_enforced=self.safe,
            shared_credential_disabled=self.safe,
            direct_ingress_blocked=self.safe,
            device_gate_ready=self.safe,
        )

    def remove(self, node, assignment):
        self.removed.append(assignment.id)
        return CredentialMutationResult(True, "removed")


@pytest.fixture(autouse=True)
def enable_assignment_features(monkeypatch):
    monkeypatch.setattr(settings, "pool_accounting_bridge_enabled", True)
    monkeypatch.setattr(settings, "unique_device_credentials_enabled", True)
    monkeypatch.setattr(settings, "per_device_rate_limit_enforced", True)
    monkeypatch.setattr(settings, "smtp_abuse_protection_enabled", True)
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)
    monkeypatch.setattr(settings, "pool_bridge_api_key", "test-pool-bridge-key")
    monkeypatch.setattr(settings, "xray_client_port_start", 20000)
    monkeypatch.setattr(settings, "xray_client_port_end", 20019)


def add_node(db_session, *, current_clients: int = 0, capacity: int = 20) -> VpnNode:
    node = VpnNode(
        region_code="de",
        name="Germany 1",
        provider="manual",
        status="active",
        endpoint="203.0.113.10",
        config_payload=BASE_CONFIG,
        device_gate_host="203.0.113.10",
        device_gate_port=24443,
        device_gate_server_name="gate.example.com",
        device_gate_spki_sha256=GATE_SPKI_SHA256,
        health_status="healthy",
        capacity_clients=capacity,
        current_clients=current_clients,
        bandwidth_limit_mbps=600,
        per_device_speed_limit_mbps=30,
    )
    db_session.add(node)
    db_session.commit()
    return node


def request(subject: str, *, expires_in_days: int = 30) -> PoolReservationPrepareRequest:
    return PoolReservationPrepareRequest(
        subject_key=subject * 64,
        entitlement_hash=("f" if subject != "f" else "e") * 64,
        entitlement_expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
        region_code="auto",
    )


def test_prepare_confirm_is_unique_and_idempotent(db_session):
    node = add_node(db_session)
    transport = FakeCredentialTransport()
    service = PoolAssignmentService(db_session, transport)

    first = service.prepare(request("a"))
    second = service.prepare(request("b"))

    assert first.node_id == node.id == second.node_id
    assert first.config != second.config
    assert BASE_UUID not in first.config
    assert "@127.0.0.1:17890?" in first.config
    assert "eg_assignment=" in first.config
    assert "eg_sni=gate.example.com" in first.config
    assert f"eg_spki={GATE_SPKI_SHA256}" in first.config
    assert db_session.get(VpnNode, node.id).current_clients == 2

    confirmed = service.confirm(
        PoolReservationConfirmRequest(
            assignment_id=first.assignment_id,
            confirmation_token=first.confirmation_token,
        )
    )
    replay = service.prepare(request("a"))

    assert confirmed.status == "active"
    assert replay.status == "active"
    assert replay.confirmation_required is False
    assert replay.config == first.config
    assert transport.installed.count(first.assignment_id) == 1
    assert db_session.get(VpnNode, node.id).current_clients == 2


def test_capacity_gate_never_oversubscribes(db_session):
    node = add_node(db_session, current_clients=19)
    service = PoolAssignmentService(db_session, FakeCredentialTransport())

    service.prepare(request("a"))
    with pytest.raises(HTTPException) as error:
        service.prepare(request("b"))

    assert error.value.status_code == 409
    assert error.value.detail == "server_capacity_unavailable"
    assert db_session.get(VpnNode, node.id).current_clients == 20


def test_unconfigured_node_is_never_selected_in_gate_mode(db_session):
    unsafe = VpnNode(
        region_code="de",
        name="Unconfigured",
        provider="manual",
        status="active",
        endpoint="203.0.113.11",
        config_payload=BASE_CONFIG,
        health_status="healthy",
        capacity_clients=20,
        current_clients=0,
        bandwidth_limit_mbps=600,
        per_device_speed_limit_mbps=30,
    )
    db_session.add(unsafe)
    db_session.commit()
    safe = add_node(db_session, current_clients=1)

    prepared = PoolAssignmentService(db_session, FakeCredentialTransport()).prepare(request("a"))

    assert prepared.node_id == safe.id
    assert db_session.get(VpnNode, unsafe.id).current_clients == 0


def test_pre_gate_active_assignment_is_reinstalled_before_reuse(db_session):
    add_node(db_session)
    transport = FakeCredentialTransport()
    service = PoolAssignmentService(db_session, transport)
    prepared = service.prepare(request("a"))
    service.confirm(
        PoolReservationConfirmRequest(
            assignment_id=prepared.assignment_id,
            confirmation_token=prepared.confirmation_token,
        )
    )
    assignment = db_session.get(VpnAssignment, prepared.assignment_id)
    assignment.device_gate_enforced = False
    db_session.commit()

    migrated = service.prepare(request("a"))

    assert migrated.status == "pending"
    assert migrated.confirmation_required is True
    assert migrated.config_revision == 2
    assert transport.installed.count(prepared.assignment_id) == 2
    assert db_session.get(VpnAssignment, prepared.assignment_id).device_gate_enforced is True


def test_maintenance_proactively_migrates_pre_gate_assignment(db_session):
    add_node(db_session)
    transport = FakeCredentialTransport()
    service = PoolAssignmentService(db_session, transport)
    prepared = service.prepare(request("a"))
    service.confirm(
        PoolReservationConfirmRequest(
            assignment_id=prepared.assignment_id,
            confirmation_token=prepared.confirmation_token,
        )
    )
    assignment = db_session.get(VpnAssignment, prepared.assignment_id)
    assignment.device_gate_enforced = False
    db_session.commit()

    result = service.run_maintenance()
    migrated = db_session.get(VpnAssignment, prepared.assignment_id)

    assert result == {"checked": 1, "migrated": 1, "revoked": 0, "failed": 0}
    assert migrated.status == "active"
    assert migrated.device_gate_enforced is True
    assert migrated.config_revision == 2
    assert transport.installed.count(prepared.assignment_id) == 2


def test_missing_safety_attestation_releases_slot(db_session):
    node = add_node(db_session)
    service = PoolAssignmentService(db_session, FakeCredentialTransport(safe=False))

    with pytest.raises(HTTPException) as error:
        service.prepare(request("a"))

    assert error.value.status_code == 503
    assert db_session.get(VpnNode, node.id).current_clients == 0
    assert db_session.query(VpnAssignment).count() == 0


def test_unconfirmed_assignment_is_revoked_and_can_be_renewed(db_session, monkeypatch):
    monkeypatch.setattr(settings, "pool_assignment_prepare_ttl_seconds", 1)
    node = add_node(db_session)
    transport = FakeCredentialTransport()
    service = PoolAssignmentService(db_session, transport)
    prepared = service.prepare(request("a"))
    assignment = db_session.get(VpnAssignment, prepared.assignment_id)
    assignment.prepare_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    result = service.run_maintenance()
    renewed = service.prepare(request("a", expires_in_days=60))

    assert result == {"checked": 1, "migrated": 0, "revoked": 1, "failed": 0}
    assert renewed.assignment_id == prepared.assignment_id
    assert renewed.config_revision == 2
    assert renewed.config != prepared.config
    assert db_session.get(VpnNode, node.id).current_clients == 1
