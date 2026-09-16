from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.backend.api import privacy_routes
from src.backend.services.node_orchestration_service import NodeOrchestrationService


def _node(node_id: int, region: str, current: int, capacity: int = 15):
    return SimpleNamespace(
        id=node_id,
        name=f"{region}-{node_id}",
        provider="manual",
        region_code=region,
        status="active",
        health_status="healthy",
        current_clients=current,
        capacity_clients=capacity,
        load_score=0,
        priority=0,
    )


class _Repo:
    def __init__(self, subscription, nodes):
        self.subscription = subscription
        self.nodes = list(nodes)
        self.assigned_node_id = None

    def get_subscription(self, subscription_id):
        return self.subscription if subscription_id == self.subscription.id else None

    def get_node(self, node_id):
        return next((node for node in self.nodes if node.id == node_id), None)

    def list_nodes(self, region_code=None):
        if region_code is None:
            return list(self.nodes)
        return [node for node in self.nodes if node.region_code == region_code]

    def assign_device_to_node(self, device, node):
        self.assigned_node_id = node.id
        device.node_id = node.id


class _Config:
    def build_import_text(self, node, subscription, device):
        return f"vless://selected-{node.id}"


class _Db:
    def commit(self):
        pass


def _orchestrator(subscription, nodes):
    service = object.__new__(NodeOrchestrationService)
    service.repo = _Repo(subscription, nodes)
    service.config_service = _Config()
    service.db = _Db()
    service.audit = SimpleNamespace(write=lambda *args, **kwargs: None)
    return service


def test_region_connect_selects_only_inside_requested_region():
    subscription = SimpleNamespace(id=1, region_code="auto")
    nodes = [_node(1, "de", 9), _node(2, "de", 2), _node(3, "nl", 0)]
    device = SimpleNamespace(node_id=None)
    service = _orchestrator(subscription, nodes)

    result = service.build_user_config_for_region(1, "DE", device)

    assert result["node"].id == 2
    assert result["node"].region_code == "de"
    assert service.repo.assigned_node_id == 2


def test_region_connect_never_spills_to_another_country():
    subscription = SimpleNamespace(id=1, region_code="auto")
    nodes = [_node(1, "de", 15), _node(2, "nl", 0)]
    service = _orchestrator(subscription, nodes)

    with pytest.raises(HTTPException) as error:
        service.build_user_config_for_region(1, "de", SimpleNamespace(node_id=None))

    assert error.value.status_code == 409
    assert error.value.detail == "server_unavailable"


def test_public_region_list_contains_no_physical_node_id(monkeypatch):
    rows = [
        {
            "id": 987654,
            "city": "Germany",
            "region_name": "Germany",
            "region_code": "de",
            "health_status": "healthy",
            "is_available": True,
            "available_nodes": 2,
        }
    ]
    fake_service = SimpleNamespace(
        node_orchestrator=SimpleNamespace(list_region_entries=lambda: rows),
    )
    monkeypatch.setattr(privacy_routes, "SubscriptionService", lambda db: fake_service)
    monkeypatch.setattr(privacy_routes, "app_update_required", lambda version: False)

    result = privacy_routes.list_vpn_regions(x_skryon_app_version_code=718, db=object())

    assert len(result) == 1
    assert result[0].model_dump() == {
        "region_code": "de",
        "name": "Germany",
        "is_available": True,
    }
    assert "987654" not in str(result[0].model_dump())
