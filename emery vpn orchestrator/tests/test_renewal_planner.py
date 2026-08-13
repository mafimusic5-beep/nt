from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.backend.services.renewal_planner_service import (
    RenewalMutationResult,
    RenewalPlannerService,
)
from src.common.config import settings
from src.common.models import VpnNode


class FakeRenewalTransport:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def disable_auto_renew(self, node):
        self.calls.append(node.id)
        return RenewalMutationResult(True, "provider_auto_renew_disabled")


def add_node(
    db_session,
    name: str,
    *,
    clients: int = 0,
    health: str = "healthy",
    price: int = 500,
) -> VpnNode:
    node = VpnNode(
        region_code="de",
        name=name,
        provider="ionos_vps_plus",
        provider_server_id=f"server-{name}",
        contract_id=f"contract-{name}",
        paid_until=datetime.now(timezone.utc) + timedelta(days=3),
        renewal_price_eur_cents=price,
        auto_renew=True,
        renewal_status="renew",
        status="active",
        endpoint=f"{name}.example.test",
        config_payload="vless://unused",
        health_status=health,
        capacity_clients=20,
        current_clients=clients,
        bandwidth_limit_mbps=600,
        per_device_speed_limit_mbps=30,
    )
    db_session.add(node)
    db_session.commit()
    return node


def test_preview_never_retires_capacity_needed_for_family_headroom(db_session):
    add_node(db_session, "busy", clients=17)
    add_node(db_session, "spare")

    plan = RenewalPlannerService(db_session).preview()

    region = plan["regions"][0]
    assert region["required_capacity"] == 22
    assert region["recommended_do_not_renew"] == []
    assert plan["actions"] == []


def test_apply_disables_renewal_but_never_deletes_or_stops_node(db_session, monkeypatch):
    keep = add_node(db_session, "keep")
    retire = add_node(db_session, "retire", health="down", price=900)
    transport = FakeRenewalTransport()
    monkeypatch.setattr(settings, "auto_renewal_actions_enabled", True)

    result = RenewalPlannerService(db_session, transport).apply()

    assert [row["status"] for row in result["results"]] == ["applied"]
    assert transport.calls == [retire.id]
    db_session.refresh(retire)
    assert retire.auto_renew is False
    assert retire.renewal_status == "do_not_renew"
    assert retire.status == "active"
    assert db_session.get(VpnNode, keep.id).auto_renew is True


def test_automatic_provider_mutation_is_fail_closed_by_default(db_session, monkeypatch):
    add_node(db_session, "keep")
    retire = add_node(db_session, "retire", health="down")
    transport = FakeRenewalTransport()
    monkeypatch.setattr(settings, "auto_renewal_actions_enabled", False)

    result = RenewalPlannerService(db_session, transport).apply()

    assert result["results"][0]["status"] == "blocked"
    assert transport.calls == []
    db_session.refresh(retire)
    assert retire.auto_renew is True
    assert retire.renewal_status == "renew"
