"""Offline-node failover is a gate revocation, not a remote Xray deletion."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.backend.schemas.pool_bridge import (
    PoolReservationConfirmRequest,
    PoolReservationQuarantineRequest,
)
from src.backend.services.pool_assignment_service import PoolAssignmentService
from src.common.config import settings
from src.common.models import VpnAssignment, VpnNode
from test_pool_assignments import FakeCredentialTransport, add_node, request


@pytest.fixture(autouse=True)
def features(monkeypatch):
    for name in (
        "pool_accounting_bridge_enabled",
        "unique_device_credentials_enabled",
        "per_device_rate_limit_enforced",
        "smtp_abuse_protection_enabled",
        "device_bound_gate_enabled",
    ):
        monkeypatch.setattr(settings, name, True)
    monkeypatch.setattr(settings, "pool_bridge_api_key", "test-pool-key")


def confirmed_assignment(db_session):
    old = add_node(db_session)
    service = PoolAssignmentService(db_session, FakeCredentialTransport())
    prepared = service.prepare(request("a"))
    service.confirm(PoolReservationConfirmRequest(
        assignment_id=prepared.assignment_id,
        confirmation_token=prepared.confirmation_token,
    ))
    return old, prepared, service


def quarantine_request(prepared, destination_id):
    return PoolReservationQuarantineRequest(
        assignment_id=prepared.assignment_id,
        subject_key="a" * 64,
        destination_node_id=destination_id,
    )


def test_quarantine_keeps_old_credential_and_accounting_then_allocates_new(db_session):
    old, prepared, service = confirmed_assignment(db_session)
    new = add_node(db_session)
    assignment = db_session.get(VpnAssignment, prepared.assignment_id)
    old_uuid = assignment.client_uuid
    old_port = assignment.client_port
    old.health_status = "down"
    assignment.status = "revocation_pending"
    assignment.last_error = "credential_ssh_failed:TimeoutError"
    db_session.commit()
    req = quarantine_request(prepared, new.id)

    result = service.quarantine_offline(req)
    assert result.status == "quarantined"
    assert service.quarantine_offline(req).status == "quarantined"
    kept = db_session.get(VpnAssignment, prepared.assignment_id)
    db_session.refresh(kept)
    assert kept.subject_key == f"retired:{prepared.assignment_id}:" + "a" * 64
    assert kept.status == "revocation_pending"
    assert kept.client_uuid == old_uuid and kept.client_port == old_port
    assert kept.device_gate_enforced is True
    assert kept.last_error == "credential_ssh_failed:TimeoutError"
    db_session.refresh(old)
    assert old.current_clients == 1  # old slot remains until confirmed cleanup

    next_request = request("a").model_copy(update={"node_id": new.id})
    new_assignment = service.prepare(next_request)
    service.confirm(PoolReservationConfirmRequest(
        assignment_id=new_assignment.assignment_id,
        confirmation_token=new_assignment.confirmation_token,
    ))
    assert new_assignment.assignment_id != prepared.assignment_id
    assert new_assignment.node_id == new.id
    db_session.refresh(new)
    assert new.current_clients == 1

    # Remote cleanup still happens through the existing maintenance loop.
    maintenance = service.run_maintenance()
    db_session.refresh(old)
    assert maintenance["revoked"] == 1
    assert old.current_clients == 0
    assert db_session.get(VpnAssignment, prepared.assignment_id).status == "revoked"
    assert db_session.get(VpnAssignment, new_assignment.assignment_id).status == "active"


def test_quarantine_refuses_node_that_is_healthy(db_session):
    old, prepared, service = confirmed_assignment(db_session)
    new = add_node(db_session)
    assignment = db_session.get(VpnAssignment, prepared.assignment_id)
    assignment.status = "revocation_pending"
    db_session.commit()
    with pytest.raises(HTTPException, match="offline_failover_requires_down_node"):
        service.quarantine_offline(quarantine_request(prepared, new.id))
    assert assignment.subject_key == "a" * 64


def test_quarantine_fails_closed_if_old_credential_lacks_gate(db_session):
    old, prepared, service = confirmed_assignment(db_session)
    new = add_node(db_session)
    assignment = db_session.get(VpnAssignment, prepared.assignment_id)
    assignment.status = "revocation_pending"
    assignment.device_gate_enforced = False
    old.health_status = "down"
    db_session.commit()
    with pytest.raises(HTTPException, match="device_gate_not_enforced"):
        service.quarantine_offline(quarantine_request(prepared, new.id))
    assert assignment.subject_key == "a" * 64
    db_session.refresh(old)
    assert old.current_clients == 1


def test_quarantine_requires_available_different_destination(db_session):
    old, prepared, service = confirmed_assignment(db_session)
    new = add_node(db_session)
    assignment = db_session.get(VpnAssignment, prepared.assignment_id)
    assignment.status = "revocation_pending"
    old.health_status = "down"
    new.health_status = "down"
    db_session.commit()
    with pytest.raises(HTTPException, match="destination_node_unavailable"):
        service.quarantine_offline(quarantine_request(prepared, new.id))
    with pytest.raises(HTTPException, match="same_node_failover_denied"):
        service.quarantine_offline(quarantine_request(prepared, old.id))
    assert assignment.subject_key == "a" * 64


def test_cached_down_node_assignment_is_not_returned(db_session):
    old, prepared, service = confirmed_assignment(db_session)
    old.health_status = "down"
    db_session.commit()
    with pytest.raises(HTTPException, match="assigned_node_unavailable"):
        service.prepare(request("a"))
    assert db_session.get(VpnAssignment, prepared.assignment_id).status == "active"


def test_fast_down_node_probe_quarantines_active_assignment_without_ssh(db_session):
    old, prepared, service = confirmed_assignment(db_session)
    new = add_node(db_session)
    old.health_status = "down"
    db_session.commit()
    req = quarantine_request(prepared, new.id)
    assert service.probe_offline_failover(req).eligible is True

    old_assignment = db_session.get(VpnAssignment, prepared.assignment_id)
    old_uuid = old_assignment.client_uuid
    result = service.quarantine_offline(req)
    assert result.status == "quarantined"
    db_session.refresh(old_assignment)
    assert old_assignment.status == "revocation_pending"
    assert old_assignment.client_uuid == old_uuid
    assert old_assignment.subject_key != "a" * 64
    # No transport.remove occurs during quarantine: only maintenance does that.
    assert service.transport.removed == []
    assert service.probe_offline_failover(req).eligible is True

    next_assignment = service.prepare(
        request("a").model_copy(update={"node_id": new.id}),
    )
    assert next_assignment.assignment_id != prepared.assignment_id
    assert next_assignment.node_id == new.id


def test_offline_probe_rejects_healthy_node_or_unprotected_credentials(db_session):
    old, prepared, service = confirmed_assignment(db_session)
    new = add_node(db_session)
    req = quarantine_request(prepared, new.id)
    assert service.probe_offline_failover(req).eligible is False
    old.health_status = "down"
    db_session.commit()
    assert service.probe_offline_failover(req).eligible is True
    assignment = db_session.get(VpnAssignment, prepared.assignment_id)
    assignment.device_gate_enforced = False
    db_session.commit()
    assert service.probe_offline_failover(req).eligible is False
