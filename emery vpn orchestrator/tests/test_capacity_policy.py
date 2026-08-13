from types import SimpleNamespace

from src.backend.services.capacity_service import CapacityService


def _node(
    node_id: int,
    region: str,
    current: int,
    *,
    capacity: int = 20,
    status: str = "active",
    health: str = "healthy",
    failures: int = 0,
    recovery_status: str = "idle",
):
    return SimpleNamespace(
        id=node_id,
        region_code=region,
        current_clients=current,
        capacity_clients=capacity,
        status=status,
        health_status=health,
        consecutive_health_failures=failures,
        recovery_status=recovery_status,
    )


class _FakeRepo:
    def __init__(self, nodes):
        self.nodes = nodes

    def list_nodes(self, region_code=None):
        if region_code is None:
            return self.nodes
        return [node for node in self.nodes if node.region_code == region_code]


def _service(nodes):
    service = object.__new__(CapacityService)
    service.node_repo = _FakeRepo(nodes)
    return service


def test_family_headroom_is_kept_until_sixteenth_device():
    row = _service([_node(1, "de", 15)]).list_regions()[0]

    assert row.free_slots == 5
    assert row.can_accept_family is True
    assert row.needs_provisioning is False
    assert row.status == "warning"


def test_sixteenth_device_requests_one_more_server():
    row = _service([_node(1, "de", 16)]).list_regions()[0]

    assert row.free_slots == 4
    assert row.can_accept_family is False
    assert row.needs_provisioning is True
    assert row.status == "scale_required"
    assert row.recommendation == "buy_one_more_server_now"


def test_pending_server_prevents_duplicate_purchase_request():
    nodes = [
        _node(1, "de", 16),
        _node(2, "de", 0, status="provisioning", health="unknown"),
    ]
    row = _service(nodes).list_regions()[0]

    assert row.pending_nodes == 1
    assert row.needs_provisioning is False


def test_recovering_server_prevents_accidental_replacement_purchase():
    nodes = [
        _node(
            1,
            "de",
            16,
            health="down",
            failures=3,
            recovery_status="restarting_xray",
        )
    ]

    row = _service(nodes).list_regions()[0]

    assert row.recovering_nodes == 1
    assert row.needs_provisioning is False


def test_down_server_is_treated_as_recovering_before_failure_counter_updates():
    row = _service([_node(1, "de", 16, health="down")]).list_regions()[0]

    assert row.recovering_nodes == 1
    assert row.needs_provisioning is False
    assert "Покупка замены заблокирована" in CapacityService._buy_recommendation(row)


def test_full_server_has_zero_admission_capacity():
    row = _service([_node(1, "de", 20)]).list_regions()[0]

    assert row.free_slots == 0
    assert row.status == "urgent"
    assert row.needs_provisioning is True


def test_equal_pressure_prefers_region_with_fewer_servers():
    service = _service(
        [
            _node(1, "de", 8),
            _node(2, "de", 8),
            _node(3, "nl", 8),
        ]
    )

    assert service.worst_region().region_code == "nl"
