from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import src.backend.services.subscription_service as subscription_module
from src.backend.schemas.pool_bridge import PoolReservationResponse
from src.backend.services.subscription_service import SubscriptionService
from src.common.config import settings
from src.common.models import Subscription, User


GATE_SPKI_SHA256 = "a" * 64


class FakePoolAssignmentService:
    requests = []
    fail = False

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _required_features_enabled():
        return True

    def prepare(self, request):
        self.requests.append(request)
        if self.fail:
            raise HTTPException(status_code=409, detail="server_capacity_unavailable")
        return PoolReservationResponse(
            assignment_id=1,
            status="pending",
            confirmation_required=True,
            confirmation_token="t" * 43,
            node_id=7,
            node_name="Germany",
            region_code="de",
            config=(
                "vless://14aec1f1-bf97-47d0-896c-c553a18e2282@127.0.0.1:17890"
                "?type=tcp&security=reality&pbk=key&sid=0123456789abcdef"
                "&eg_v=1&eg_host=203.0.113.10&eg_port=24443&eg_sni=gate.example.com"
                f"&eg_spki={GATE_SPKI_SHA256}"
                "&eg_assignment=1&eg_node=7#Germany"
            ),
            client_port=20000,
            device_gate_required=True,
            device_gate_host="203.0.113.10",
            device_gate_port=24443,
            device_gate_server_name="gate.example.com",
            device_gate_spki_sha256=GATE_SPKI_SHA256,
            config_revision=1,
            speed_limit_mbps=30,
            entitlement_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )

    def confirm(self, request):
        return None


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
    FakePoolAssignmentService.fail = False
    monkeypatch.setattr(subscription_module, "PoolAssignmentService", FakePoolAssignmentService)
    monkeypatch.setattr(settings, "pool_bridge_api_key", "native-test-secret")


def test_native_registration_reserves_and_confirms_one_pool_slot(db_session):
    sub = add_subscription(db_session)

    device = SubscriptionService(db_session)._register_device_inner(
        sub.id,
        "stable-native-device",
        "android",
        "Phone",
    )

    assert device.id is not None
    assert len(FakePoolAssignmentService.requests) == 1
    request = FakePoolAssignmentService.requests[0]
    assert request.subject_type == "native_device"
    assert len(request.subject_key) == 64
    assert "stable-native-device" not in request.subject_key


def test_native_registration_is_removed_when_pool_is_full(db_session):
    sub = add_subscription(db_session)
    FakePoolAssignmentService.fail = True

    with pytest.raises(HTTPException) as error:
        SubscriptionService(db_session)._register_device_inner(
            sub.id,
            "no-capacity-device",
            "android",
            "Phone",
        )

    assert error.value.detail == "server_capacity_unavailable"
    assert SubscriptionService(db_session).repo.count_active_devices(sub.id) == 0


def test_unsigned_native_contract_fails_closed_in_device_gate_mode(db_session, monkeypatch):
    sub = add_subscription(db_session)
    monkeypatch.setattr(settings, "device_bound_gate_enabled", True)

    with pytest.raises(HTTPException) as error:
        SubscriptionService(db_session)._register_device_inner(
            sub.id,
            "unsigned-native-device",
            "android",
            "Phone",
        )

    assert error.value.status_code == 503
    assert error.value.detail == "native_device_key_binding_required"
    assert FakePoolAssignmentService.requests == []
    assert SubscriptionService(db_session).repo.count_active_devices(sub.id) == 0
