from datetime import datetime, timedelta, timezone

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.subscription_repo import SubscriptionRepository
from src.common.models import AuditLog, Device, Subscription, User


def _device_fixture(db_session):
    now = datetime.now(timezone.utc)
    user = User(telegram_id=1001)
    db_session.add(user)
    db_session.flush()
    subscription = Subscription(
        user_id=user.id,
        plan_code="personal",
        region_code="auto",
        status="active",
        devices_limit=1,
        starts_at=now,
        ends_at=now + timedelta(days=30),
    )
    db_session.add(subscription)
    db_session.flush()
    device = Device(
        subscription_id=subscription.id,
        device_fingerprint="privacy-test-device",
        platform="android",
        device_name="Android-устройство",
        is_active=True,
        last_seen_at=None,
    )
    db_session.add(device)
    db_session.commit()
    return subscription, device


def test_user_activity_is_not_written_to_audit_log(db_session):
    audit = AuditRepository(db_session)
    audit.write("user", "123", "vpn_connect_requested", "vpn_node", "7")
    db_session.commit()
    assert db_session.query(AuditLog).count() == 0

    audit.write("admin", "api", "node_provision_requested", "vpn_node", "7")
    db_session.commit()
    assert db_session.query(AuditLog).count() == 1


def test_heartbeat_does_not_create_activity_history(db_session):
    subscription, device = _device_fixture(db_session)
    repo = SubscriptionRepository(db_session)

    assert repo.heartbeat(subscription.id, device.device_fingerprint) is True
    db_session.commit()
    db_session.refresh(device)
    assert device.last_seen_at is None


def test_existing_device_upsert_does_not_store_client_metadata(db_session):
    subscription, device = _device_fixture(db_session)
    device.slot_index = 1
    db_session.commit()
    repo = SubscriptionRepository(db_session)

    result = repo.upsert_device(
        subscription.id,
        device.device_fingerprint,
        "some-platform",
        "Some identifying phone name",
        1,
    )
    db_session.commit()
    db_session.refresh(device)

    assert result is not None
    assert device.last_seen_at is None
    assert device.platform == "android"
    assert device.device_name == "Android-устройство"
