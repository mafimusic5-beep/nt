from __future__ import annotations

from src.backend.services.node_self_healing_service import (
    NodeSelfHealingService,
    reset_self_healing_state,
)
from src.common.config import settings
from src.common.models import VpnNode


class FakeClient:
    def __init__(self) -> None:
        self.reboots: list[tuple[str, bool]] = []

    def reboot_vds(self, vps_id: str, *, hard: bool = False) -> dict:
        self.reboots.append((vps_id, hard))
        return {"doc": {"ok": {}}}


class FakeProvisioning:
    def __init__(self) -> None:
        self.client = FakeClient()


def _make_node(db_session) -> VpnNode:
    node = VpnNode(
        region_code="frankfurt",
        name="fra-1",
        provider="firstvds",
        status="active",
        endpoint="203.0.113.10",
        health_status="healthy",
        load_score=0,
        capacity_clients=30,
        current_clients=0,
        bandwidth_limit_mbps=1000,
        per_device_speed_limit_mbps=50,
        firstvds_vps_id="vps-42",
    )
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)
    return node


def test_unhealthy_node_is_quarantined_then_rebooted(db_session, monkeypatch) -> None:
    reset_self_healing_state()
    monkeypatch.setattr(settings, "health_self_heal_enabled", True)
    monkeypatch.setattr(settings, "health_self_heal_failure_threshold", 3)
    monkeypatch.setattr(settings, "health_self_heal_cooldown_seconds", 0)
    monkeypatch.setattr(settings, "health_self_heal_max_attempts", 3)
    monkeypatch.setattr(settings, "health_self_heal_retry_window_seconds", 60)
    monkeypatch.setattr(settings, "health_self_heal_hard_reboot_after_attempt", 3)

    node = _make_node(db_session)
    provisioning = FakeProvisioning()
    service = NodeSelfHealingService(db_session, provisioning)

    for expected_streak in (1, 2):
        result = service.process(
            {"results": [{"node_id": node.id, "health_status": "down", "reason": "port_closed"}]}
        )
        db_session.refresh(node)
        assert node.status == "recovering"
        assert result["actions"] == []
        assert provisioning.client.reboots == []

    result = service.process(
        {"results": [{"node_id": node.id, "health_status": "down", "reason": "port_closed"}]}
    )
    db_session.refresh(node)

    assert node.status == "recovering"
    assert provisioning.client.reboots == [("vps-42", False)]
    assert result["actions"][0]["action"] == "reboot_vps"
    assert result["actions"][0]["attempt"] == 1

    recovered = service.process(
        {"results": [{"node_id": node.id, "health_status": "healthy", "reason": "port_open"}]}
    )
    db_session.refresh(node)

    assert node.status == "active"
    assert recovered["actions"][0]["status"] == "recovered"
    reset_self_healing_state()


def test_third_recovery_attempt_escalates_to_hard_reboot(db_session, monkeypatch) -> None:
    reset_self_healing_state()
    monkeypatch.setattr(settings, "health_self_heal_enabled", True)
    monkeypatch.setattr(settings, "health_self_heal_failure_threshold", 1)
    monkeypatch.setattr(settings, "health_self_heal_cooldown_seconds", 0)
    monkeypatch.setattr(settings, "health_self_heal_max_attempts", 3)
    monkeypatch.setattr(settings, "health_self_heal_retry_window_seconds", 60)
    monkeypatch.setattr(settings, "health_self_heal_hard_reboot_after_attempt", 3)

    node = _make_node(db_session)
    provisioning = FakeProvisioning()
    service = NodeSelfHealingService(db_session, provisioning)

    for _ in range(3):
        service.process(
            {"results": [{"node_id": node.id, "health_status": "down", "reason": "port_closed"}]}
        )

    assert provisioning.client.reboots == [
        ("vps-42", False),
        ("vps-42", False),
        ("vps-42", True),
    ]
    reset_self_healing_state()
