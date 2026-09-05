from sqlalchemy import select

from src.backend.schemas.admin import ManualNodeBootstrapRequest, VpnNodeUpsertRequest
from src.backend.services.admin_service import AdminService
from src.backend.services.manual_node_admin_service import ManualNodeAdminService
from src.backend.services.manual_node_bootstrap_service import ManualNodeBootstrapService
from src.common.config import settings
from src.common.models import VpnNode


def test_manual_bootstrap_reuses_existing_endpoint_after_reimage(db_session, monkeypatch):
    monkeypatch.setattr(settings, "device_bound_gate_enabled", False)

    created = AdminService(db_session).create_node(
        VpnNodeUpsertRequest(
            name="Old server",
            region_code="de",
            endpoint="82.165.163.77",
            config_payload="",
            status="active",
            health_status="healthy",
            load_score=10,
            priority=0,
            capacity_clients=5,
            bandwidth_limit_mbps=1000,
            current_clients=3,
            per_device_speed_limit_mbps=100,
        )
    )
    node = db_session.get(VpnNode, created.id)
    assert node is not None
    node.ssh_host_key = "old-host-key"
    node.ssh_key_status = "installed"
    db_session.commit()

    old_id = created.id

    def fake_bootstrap(self, target, *, ssh_user, ssh_password):
        assert target.id == old_id
        assert target.status == "provisioning"
        assert target.health_status == "unknown"
        assert target.config_payload == ""
        assert target.ssh_host_key == ""
        assert target.ssh_key_status == "missing"
        # Existing assignments/capacity accounting stay attached to the same
        # node identity while the physical VPS is rebuilt.
        assert target.current_clients == 3
        assert ssh_user == "root"
        assert ssh_password == "new-root-password"
        target.config_payload = "vless://reinstalled"
        target.ssh_key_status = "installed"
        return {"status": "ok", "isp_egress_enabled": False}

    monkeypatch.setattr(ManualNodeBootstrapService, "bootstrap_with_password", fake_bootstrap)

    result = ManualNodeAdminService(db_session).bootstrap(
        ManualNodeBootstrapRequest(
            name="Germany",
            region_code="de",
            endpoint="82.165.163.77",
            ssh_user="root",
            ssh_password="new-root-password",
            capacity_clients=5,
            bandwidth_limit_mbps=1000,
            per_device_speed_limit_mbps=100,
        )
    )

    assert result.node.id == old_id
    assert result.node.endpoint == "82.165.163.77"
    assert result.node.status == "active"
    assert result.node.health_status == "healthy"
    assert result.node.current_clients == 3
    assert len(db_session.scalars(select(VpnNode)).all()) == 1
