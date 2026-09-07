from src.backend.services.admin_service import AdminService


def test_admin_node_speed_limit_is_capped_at_50_mbps():
    assert AdminService._effective_speed_limit(100) == 50
    assert AdminService._effective_speed_limit(50) == 50
    assert AdminService._effective_speed_limit(30) == 30
    assert AdminService._effective_speed_limit(1) == 1
