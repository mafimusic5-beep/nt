from types import SimpleNamespace

from src.backend.services.node_orchestration_service import NodeOrchestrationService


def _node(
    node_id: int,
    region: str,
    current: int,
    capacity: int = 15,
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
    one_third = _node(1, "de", current=5)
    two_thirds = _node(2, "de", current=10)

    selected = sorted(
        [two_thirds, one_third],
        key=NodeOrchestrationService._node_sort_key,
    )[0]

    assert selected.id == one_third.id


def test_fill_percentage_is_primary_over_health_tiebreaker():
    degraded_but_empty = _node(3, "de", current=0, health="degraded")
    healthy_but_busy = _node(4, "de", current=12, health="healthy")

    selected = sorted(
        [healthy_but_busy, degraded_but_empty],
        key=NodeOrchestrationService._node_sort_key,
    )[0]

    assert selected.id == degraded_but_empty.id


def test_connect_reselects_freest_server_inside_requested_region():
    requested_full = _node(10, "de", current=15)
    freest_de = _node(11, "de", current=3)
    busier_de = _node(12, "de", current=9)
    free_other_region = _node(20, "nl", current=0)
    subscription = SimpleNamespace(id=7, region_code="moscow")
    device = SimpleNamespace(node_id=None)
    service = _service(
        subscription,
        [requested_full, freest_de, busier_de, free_other_region],
    )

    result = service.build_user_config_for_node(
        subscription_id=subscription.id,
        node_id=requested_full.id,
        device=device,
    )

    assert result["node"].id == freest_de.id
    assert result["node"].region_code == "de"
    assert result["import_text"] == f"vless://selected-{freest_de.id}"
    assert service.repo.assigned_node_id == freest_de.id


def test_regional_pool_never_spills_into_another_region():
    full_de = _node(30, "de", current=15)
    also_full_de = _node(31, "de", current=15)
    free_nl = _node(40, "nl", current=0)
    subscription = SimpleNamespace(id=8, region_code="moscow")
    device = SimpleNamespace(node_id=None)
    service = _service(subscription, [full_de, also_full_de, free_nl])

    try:
        service.build_user_config_for_node(
            subscription_id=subscription.id,
            node_id=full_de.id,
            device=device,
        )
    except Exception as error:
        assert getattr(error, "status_code", None) == 409
        assert getattr(error, "detail", None) == "server_unavailable"
    else:
        raise AssertionError("regional pool must not spill into another region")


def test_reconnect_does_not_penalize_device_already_on_node():
    current_node = _node(1, "de", current=5)
    other_node = _node(2, "de", current=4)
    device = SimpleNamespace(node_id=current_node.id)

    selected = sorted(
        [other_node, current_node],
        key=lambda node: NodeOrchestrationService._node_sort_key(node, device),
    )[0]

    assert selected.id == current_node.id


def test_fifteenth_client_fills_node_and_sixteenth_is_not_connectable():
    almost_full = _node(50, "de", current=14)
    full = _node(51, "de", current=15)

    assert NodeOrchestrationService._is_connectable(almost_full) is True
    assert NodeOrchestrationService._is_connectable(full) is False
