from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from src.backend.services.admin_node_assignment_cleanup_service import (
    AdminNodeAssignmentCleanupService,
)
from src.backend.services.xray_credential_service import CredentialMutationResult
from src.common.models import VpnAssignment, VpnNode


class FakeTransport:
    def __init__(self, *, fail_ids: set[int] | None = None) -> None:
        self.fail_ids = fail_ids or set()
        self.removed: list[int] = []

    def remove(self, node, assignment):
        self.removed.append(assignment.id)
        if assignment.id in self.fail_ids:
            return CredentialMutationResult(False, "remove_failed")
        return CredentialMutationResult(True, "ok")


def _node(db_session, *, current_clients: int) -> VpnNode:
    node = VpnNode(
        region_code="auto",
        name="Server",
        provider="manual",
        status="active",
        endpoint="203.0.113.50",
        config_payload="",
        health_status="healthy",
        load_score=0,
        priority=0,
        capacity_clients=15,
        current_clients=current_clients,
        bandwidth_limit_mbps=1000,
        per_device_speed_limit_mbps=30,
    )
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)
    return node


def _assignment(db_session, node: VpnNode, key: str, port: int) -> VpnAssignment:
    row = VpnAssignment(
        subject_type="legacy_device",
        subject_key=key,
        entitlement_hash=f"entitlement-{key}",
        entitlement_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        node_id=node.id,
        client_uuid=f"00000000-0000-4000-8000-{port:012d}",
        client_port=port,
        speed_limit_mbps=30,
        status="active",
        device_gate_enforced=True,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_cleanup_repairs_counter_when_no_assignments_remain(db_session):
    node = _node(db_session, current_clients=15)

    result = AdminNodeAssignmentCleanupService(
        db_session,
        transport=FakeTransport(),
        sleep_fn=lambda _seconds: None,
    ).clear(node.id)

    db_session.refresh(node)
    assert result["cleared"] == 0
    assert result["remaining"] == 0
    assert node.current_clients == 0
    assert node.status == "active"
    assert node.health_status == "healthy"


def test_cleanup_revokes_counted_assignments_and_frees_slots(db_session):
    node = _node(db_session, current_clients=2)
    first = _assignment(db_session, node, "device-a", 20000)
    second = _assignment(db_session, node, "device-b", 20001)
    transport = FakeTransport()

    result = AdminNodeAssignmentCleanupService(
        db_session,
        transport=transport,
        sleep_fn=lambda _seconds: None,
    ).clear(node.id)

    db_session.refresh(node)
    db_session.refresh(first)
    db_session.refresh(second)
    assert result == {
        "node_id": node.id,
        "cleared": 2,
        "failed": 0,
        "remaining": 0,
        "node_status": "active",
        "health_status": "healthy",
    }
    assert transport.removed == [first.id, second.id]
    assert first.status == "revoked"
    assert second.status == "revoked"
    assert node.current_clients == 0


def test_cleanup_fails_closed_when_remote_credential_removal_fails(db_session):
    node = _node(db_session, current_clients=1)
    assignment = _assignment(db_session, node, "device-a", 20000)
    transport = FakeTransport(fail_ids={assignment.id})

    result = AdminNodeAssignmentCleanupService(
        db_session,
        transport=transport,
        sleep_fn=lambda _seconds: None,
    ).clear(node.id)

    db_session.refresh(node)
    db_session.refresh(assignment)
    assert result["failed"] == 1
    assert result["remaining"] == 1
    assert assignment.status == "revocation_pending"
    assert node.current_clients == 1
    assert node.status == "maintenance"
    assert node.health_status == "down"


def test_cleanup_rejects_second_run_while_lease_is_active(db_session):
    node = _node(db_session, current_clients=1)
    assignment = _assignment(db_session, node, "device-a", 20000)
    assignment.status = "revoking"
    assignment.prepare_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_session.commit()
    transport = FakeTransport()

    with pytest.raises(HTTPException) as exc_info:
        AdminNodeAssignmentCleanupService(
            db_session,
            transport=transport,
            sleep_fn=lambda _seconds: None,
        ).clear(node.id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "node_assignment_cleanup_in_progress"
    assert transport.removed == []


def test_cleanup_resumes_expired_revoking_lease(db_session):
    node = _node(db_session, current_clients=1)
    assignment = _assignment(db_session, node, "device-a", 20000)
    assignment.status = "revoking"
    assignment.prepare_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    transport = FakeTransport()

    result = AdminNodeAssignmentCleanupService(
        db_session,
        transport=transport,
        sleep_fn=lambda _seconds: None,
    ).clear(node.id)

    db_session.refresh(assignment)
    db_session.refresh(node)
    assert result["cleared"] == 1
    assert result["remaining"] == 0
    assert transport.removed == [assignment.id]
    assert assignment.status == "revoked"
    assert node.current_clients == 0
