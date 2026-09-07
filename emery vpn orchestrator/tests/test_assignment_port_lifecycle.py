from datetime import datetime, timedelta, timezone
import uuid

from src.backend.core.assignment_port_lifecycle import install_assignment_port_lifecycle
from src.common.models import VpnAssignment, VpnNode


def test_revoked_assignment_releases_port_for_reuse(db_session):
    install_assignment_port_lifecycle()
    install_assignment_port_lifecycle()

    node = VpnNode(
        region_code="de",
        name="Germany 1",
        provider="manual",
        status="active",
        endpoint="203.0.113.10",
        health_status="healthy",
        capacity_clients=20,
        current_clients=1,
    )
    db_session.add(node)
    db_session.commit()

    assignment = VpnAssignment(
        subject_type="legacy_device",
        subject_key="a" * 64,
        entitlement_hash="b" * 64,
        entitlement_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        node_id=node.id,
        client_uuid=str(uuid.uuid4()),
        client_port=20000,
        speed_limit_mbps=30,
        status="active",
        device_gate_enforced=True,
    )
    db_session.add(assignment)
    db_session.commit()
    assignment_id = assignment.id

    assignment.status = "revoked"
    assignment.device_gate_enforced = False
    db_session.commit()
    db_session.refresh(assignment)

    assert assignment.client_port == -assignment_id

    replacement = VpnAssignment(
        subject_type="legacy_device",
        subject_key="c" * 64,
        entitlement_hash="d" * 64,
        entitlement_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        node_id=node.id,
        client_uuid=str(uuid.uuid4()),
        client_port=20000,
        speed_limit_mbps=30,
        status="installing",
    )
    db_session.add(replacement)
    db_session.commit()

    assert replacement.id is not None
    assert replacement.client_port == 20000
