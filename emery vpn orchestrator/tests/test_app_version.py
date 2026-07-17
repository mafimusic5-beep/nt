from src.backend.api.compat_routes import AuthKeyRequestBody, auth_key, profile
from src.backend.api.routes import get_vpn_servers
from src.backend.schemas.admin import GrantSubscriptionRequest, VpnNodeUpsertRequest
from src.backend.services.admin_service import AdminService
from src.backend.utils.app_version import app_update_required
from src.common.config import settings


VALID_CONFIG = (
    "vless://00000000-0000-0000-0000-000000000000@1.2.3.4:443"
    "?encryption=none&flow=xtls-rprx-vision&security=reality&sni=example.com"
    "&fp=chrome&pbk=public-key&sid=abcd&type=tcp#Germany"
)
UPDATE_MESSAGE = "Версия приложения устарела. Обновите приложение."


def test_old_or_unversioned_client_requires_update(monkeypatch):
    monkeypatch.setattr(settings, "min_supported_app_version_code", 716)

    assert app_update_required(None) is True
    assert app_update_required(0) is True
    assert app_update_required(715) is True
    assert app_update_required(716) is False


def test_old_server_list_contract_shows_update_message(db_session, monkeypatch):
    monkeypatch.setattr(settings, "min_supported_app_version_code", 716)
    monkeypatch.setattr(settings, "app_update_message", UPDATE_MESSAGE)
    AdminService(db_session).create_node(
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

    old_rows = get_vpn_servers(x_skryon_app_version_code=0, db=db_session)
    current_rows = get_vpn_servers(x_skryon_app_version_code=716, db=db_session)

    assert old_rows == [
        {
            "id": 0,
            "city": UPDATE_MESSAGE,
            "health_status": "upgrade_required",
            "is_available": True,
        }
    ]
    assert current_rows[0]["city"] == "Germany"


def test_old_profile_contract_shows_update_message(db_session, monkeypatch):
    monkeypatch.setattr(settings, "min_supported_app_version_code", 716)
    monkeypatch.setattr(settings, "app_update_message", UPDATE_MESSAGE)
    admin_service = AdminService(db_session)
    admin_service.grant_subscription(
        GrantSubscriptionRequest(telegram_id=716, months=1, region_code="moscow")
    )
    access_key = admin_service.generate_code(telegram_id=716)["activation_code"]

    activated = auth_key(
        AuthKeyRequestBody(key=access_key),
        x_skryon_app_version_code=0,
        db=db_session,
    )
    refreshed = profile(
        access_key=access_key,
        x_skryon_app_version_code=715,
        db=db_session,
    )
    current = profile(
        access_key=access_key,
        x_skryon_app_version_code=716,
        db=db_session,
    )

    assert activated["valid"] is True
    assert activated["vpn_enabled"] is False
    assert activated["plan_name"] == UPDATE_MESSAGE
    assert activated["update_required"] is True
    assert refreshed["vpn_enabled"] is False
    assert refreshed["message"] == UPDATE_MESSAGE
    assert current["vpn_enabled"] is True
    assert "update_required" not in current
