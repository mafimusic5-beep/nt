from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import src.backend.services.subscription_service as subscription_module
from src.backend.services.subscription_service import SubscriptionService
from src.common.config import settings
from src.common.models import Subscription, User


class FakePoolAssignmentService:
    requests = []

    def __init__(self, db):
        self.db = db

    def prepare(self, request):
        self.requests.append(request)
        raise AssertionError("registration must not reserve a VPN pool assignment")


def add_subscription(db_session) -> Subscription:
    user = User(telegram_id=991122)
    db_session.add(user)
    db_session.flush()
    sub = Subscription(
        user_id=user.id,
        plan_code="personal_1m",
        region_code="de",
        status="active",
        devices_limit=1,
        starts_at=datetime.now(timezone.utc),
        ends_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(sub)
    db_session.commit()
    return sub


@pytest.fixture(autouse=True)
def fake_pool(monkeypatch):
    FakePoolAssignmentService.requests = []
    monkeypatch.setattr(subscription_module, "PoolAssignmentService", FakePoolAssignmentService)
    monkeypatch.setattr(settings, "pool_bridge_api_key", "native-test-secret")


def register(db_session, sub: Subscription, device_id: str):
    return SubscriptionService(db_session)._register_device_inner(
        sub.id,
        device_id,
        "android",
        "Phone",
    )


def test_native_registration_does_not_reserve_pool_assignment(db_session):
    sub = add_subscription(db_session)
    device = register(db_session, sub, "stable-native-device")

    assert device.id is not None
    assert FakePoolAssignmentService.requests == []
    assert SubscriptionService(db_session).repo.count_active_devices(sub.id) == 1


def test_personal_plan_allows_multiple_registered_installations(db_session):
    sub = add_subscription(db_session)
    first = register(db_session, sub, "device-one")
    second = register(db_session, sub, "device-two")

    assert first.id is not None
    assert second.id is not None
    assert first.slot_index is None
    assert second.slot_index is None
    assert SubscriptionService(db_session).repo.count_active_devices(sub.id) == 2
    assert FakePoolAssignmentService.requests == []


def test_device_gate_mode_does_not_bind_pool_during_registration(db_session, monkeypatch):
    sub = add_subscription(db_session)
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)

    device = register(db_session, sub, "unsigned-native-device")

    assert device.id is not None
    assert FakePoolAssignmentService.requests == []
