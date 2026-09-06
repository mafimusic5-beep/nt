from datetime import datetime, timedelta, timezone

from src.backend.services.admin_node_delete_service import AdminNodeDeleteService
from src.common.models import VpnAssignment, VpnNode


BASE_CONFIG = (
    "vless://11111111-1111-4111-8111-111111111111@203.0.113.10:443"
    "?encryption=none&security=reality&type=tcp&sni=example.com&fp=chrome"
    "&pbk=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&sid=abcd#Server"
)


def _node(db_session, *, current_clients: int = 0) -> VpnNode:
    node = VpnNode(
        region_code="auto",
        name="Server",
        provider="manual",
        status="maintenance",
        endpoint="203.0.113.10",
        config_payload=BASE_CONFIG,
        health_status="down",
        capacity_clients=20,
        current_clients=current_clients,
        bandwidth_limit_mbps=1000,
        per_device_speed_limit_mbps=30,
    )
    db_session.add(node)
    db_session.commit()
    return node


def test_delete_node_removes_disabled_zero_slot_node(db_session):
    node = _node(db_session)

    result = AdminNodeDeleteService(db_session).delete(node.id)

    assert result["status"] == "ok"
    assert result["detail"] == "deleted"
    assert db_session.get(VpnNode, node.id) is None


def test_delete_node_cleans_counted_assignments_before_delete(db_session):
    node = _node(db_session, current_clients=1)
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
        config_revision=1,
        device_gate_enforced=True,
    )
    db_session.add(assignment)
    db_session.commit()

    class FakeCleanup:
        def clear(self, node_id: int):
            row = db_session.get(VpnAssignment, assignment.id)
            row.status = "revoked"
            target = db_session.get(VpnNode, node_id)
            target.current_clients = 0
            db_session.commit()
            return {"cleared": 1, "failed": 0, "remaining": 0}

    result = AdminNodeDeleteService(db_session, cleanup_service=FakeCleanup()).delete(node.id)

    assert result["removed_assignments"] == 1
    assert db_session.get(VpnAssignment, assignment.id) is None
    assert db_session.get(VpnNode, node.id) is None
