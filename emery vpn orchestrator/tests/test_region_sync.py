from src.backend.api.compat_routes import _region_revision_snapshot
from src.backend.schemas.admin import VpnNodeUpsertRequest
from src.backend.services.admin_service import AdminService
from src.backend.services.subscription_service import SubscriptionService


VALID_CONFIG = (
    "vless://00000000-0000-0000-0000-000000000000@1.2.3.4:443"
    "?encryption=none&flow=xtls-rprx-vision&security=reality&sni=example.com"
    "&fp=chrome&pbk=public-key&sid=abcd&type=tcp#Germany"
)


def test_disabling_node_changes_old_client_revision_and_hides_server(db_session):
    service = AdminService(db_session)
    node = service.create_node(
        VpnNodeUpsertRequest(
            name="Germany",
            region_code="legacy-7",
            provider="skryon-legacy",
            endpoint="1.2.3.4",
            config_payload=VALID_CONFIG,
            status="active",
            health_status="healthy",
        )
    )

    before_revision, before_count = _region_revision_snapshot(db_session)
    before_rows = SubscriptionService(db_session).list_vpn_servers()
    assert before_count == 1
    assert before_rows[0]["city"] == "Germany"
    assert before_rows[0]["is_available"] is True

    result = service.set_node_enabled(node.id, False)
    after_revision, after_count = _region_revision_snapshot(db_session)
    after_rows = SubscriptionService(db_session).list_vpn_servers()

    assert result["detail"] == "disabled"
    assert after_count == 1
    assert after_revision != before_revision
    assert after_rows[0]["is_available"] is False
