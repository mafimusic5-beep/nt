from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.backend.repositories.subscription_repo import SubscriptionRepository
from src.backend.schemas.internal import ConfirmPaymentRequest, CreateOrderRequest
from src.backend.schemas.subscription import RedeemActivationCodeRequest
from src.backend.services.order_service import OrderService
from src.backend.services.subscription_service import SubscriptionService
from src.common.models import Device, Subscription, User


@pytest.mark.parametrize(
    ("plan_code", "expected_limit"),
    [
        ("personal_1m", 1),
        ("personal_plus_1m", 2),
        ("family_1m", 5),
    ],
)
def test_paid_tariff_limit_is_concurrency_not_registration(
    db_session,
    plan_code: str,
    expected_limit: int,
) -> None:
    telegram_id = {1: 1001, 2: 1002, 5: 1005}[expected_limit]
    orders = OrderService(db_session)
    subscriptions = SubscriptionService(db_session)
    order = orders.create_order(CreateOrderRequest(telegram_id=telegram_id, plan_code=plan_code))
    paid = orders.confirm_payment(
        ConfirmPaymentRequest(
            order_id=order.order_id,
            provider_payment_id=f"pay-{plan_code}",
            idempotency_key=f"idem-{plan_code}",
        )
    )

    registration_count = expected_limit + 3
    for index in range(registration_count):
        subscriptions.redeem_code(
            RedeemActivationCodeRequest(
                code=paid.activation_code,
                telegram_id=telegram_id,
                device_fingerprint=f"{plan_code}-device-{index}",
                platform="android",
                device_name=f"Phone {index}",
            )
        )

    status = subscriptions.get_status(telegram_id)
    assert status.plan_code == plan_code
    assert status.devices_used == 0
    assert status.devices_limit == expected_limit
    assert subscriptions.repo.count_active_devices(paid.subscription_id) == registration_count

    slots = list(
        db_session.scalars(
            select(Device.slot_index)
            .where(Device.subscription_id == paid.subscription_id)
        ).all()
    )
    assert slots == [None] * registration_count


def test_repository_registration_is_unlimited_for_personal_plan(db_session) -> None:
    user = User(telegram_id=909090)
    db_session.add(user)
    db_session.flush()
    subscription = Subscription(
        user_id=user.id,
        plan_code="personal_1m",
        region_code="de",
        status="active",
        devices_limit=1,
        starts_at=datetime.now(timezone.utc),
        ends_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(subscription)
    db_session.commit()
    repo = SubscriptionRepository(db_session)

    first = repo.upsert_device(
        subscription.id, "first-device", "android", "First phone", devices_limit=1
    )
    second = repo.upsert_device(
        subscription.id, "second-device", "android", "Second phone", devices_limit=1
    )
    db_session.commit()

    assert first is not None and second is not None
    assert first.slot_index is None
    assert second.slot_index is None
    assert repo.count_active_devices(subscription.id) == 2
