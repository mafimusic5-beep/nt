from types import SimpleNamespace

from src.backend.services.node_orchestration_service import NodeOrchestrationService


def _node(
    node_id: int,
    region: str,
    current: int,
    capacity: int,
    *,
    health: str = "healthy",
    load_score: int = 100,
    priority: int = 0,
):
    return SimpleNamespace(
        id=node_id,
        name=f"{region}-{node_id}",
        provider="manual",
        region_code=region,
        status="active",
        health_status=health,
        current_clients=current,
        capacity_clients=capacity,
        load_score=load_score,
        priority=priority,
    )


class _FakeRepo:
    def __init__(self, subscription, nodes):
        self.subscription = subscription
        self.nodes = {node.id: node for node in nodes}
        self.assigned_node_id = None

    def get_subscription(self, subscription_id):
        return self.subscription if subscription_id == self.subscription.id else None

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def list_nodes(self, region_code=None):
        nodes = list(self.nodes.values())
        if region_code is not None:
            nodes = [node for node in nodes if node.region_code == region_code]
        return nodes

    def assign_device_to_node(self, device, node):
        self.assigned_node_id = node.id
        device.node_id = node.id


class _FakeConfigService:
    def build_import_text(self, node, subscription, device):
        return f"vless://selected-{node.id}"


class _FakeDb:
    def commit(self):
        pass


def _service(subscription, nodes):
    service = object.__new__(NodeOrchestrationService)
    service.repo = _FakeRepo(subscription, nodes)
    service.config_service = _FakeConfigService()
    service.db = _FakeDb()
    service.audit = SimpleNamespace(write=lambda *args, **kwargs: None)
    return service


def test_node_sort_prefers_lowest_fill_percentage():
    twenty_percent = _node(1, "de", current=20, capacity=100)
    fifty_percent = _node(2, "de", current=5, capacity=10)

    selected = sorted(
        [fifty_percent, twenty_percent],
        key=NodeOrchestrationService._node_sort_key,
    )[0]

    assert selected.id == twenty_percent.id


def test_fill_percentage_is_primary_over_health_tiebreaker():
    degraded_but_empty = _node(3, "de", current=0, capacity=100, health="degraded")
    healthy_but_busy = _node(4, "de", current=80, capacity=100, health="healthy")

    selected = sorted(
        [healthy_but_busy, degraded_but_empty],
        key=NodeOrchestrationService._node_sort_key,
    )[0]

    assert selected.id == degraded_but_empty.id


def test_connect_reselects_freest_server_inside_requested_region():
    requested = _node(10, "de", current=90, capacity=100)
    freest_de = _node(11, "de", current=3, capacity=20)
    busier_de = _node(12, "de", current=40, capacity=100)
    free_other_region = _node(20, "nl", current=0, capacity=100)
    subscription = SimpleNamespace(id=7, region_code="moscow")
    device = SimpleNamespace(node_id=None)
    service = _service(
        subscription,
        [requested, freest_de, busier_de, free_other_region],
    )

    result = service.build_user_config_for_node(
        subscription_id=subscription.id,
        node_id=requested.id,
        device=device,
    )

    assert result["node"].id == freest_de.id
    assert result["import_text"] == f"vless://selected-{freest_de.id}"
    assert service.repo.assigned_node_id == freest_de.id


def test_reconnect_does_not_penalize_device_already_on_node():
    current_node = _node(1, "de", current=5, capacity=10)
    other_node = _node(2, "de", current=4, capacity=10)
    device = SimpleNamespace(node_id=current_node.id)

    selected = sorted(
        [other_node, current_node],
        key=lambda node: NodeOrchestrationService._node_sort_key(node, device),
    )[0]

    assert selected.id == current_node.id
