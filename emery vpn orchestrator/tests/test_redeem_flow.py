import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.backend.schemas.internal import ConfirmPaymentRequest, CreateOrderRequest
from src.backend.schemas.subscription import RedeemActivationCodeRequest
from src.backend.services.order_service import OrderService
from src.backend.services.subscription_service import SubscriptionService
from src.common.models import ActivationCode


def test_redeem_flow_success_and_repeat_device_add(db_session) -> None:
    order_service = OrderService(db_session)
    sub_service = SubscriptionService(db_session)

    order = order_service.create_order(CreateOrderRequest(telegram_id=333, plan_code="warmup_3m"))
    payment = order_service.confirm_payment(
        ConfirmPaymentRequest(
            order_id=order.order_id,
            provider_payment_id="pay_redeem_1",
            idempotency_key="idem_redeem_1",
            paid=True,
        )
    )

    first = sub_service.redeem_code(
        RedeemActivationCodeRequest(
            code=payment.activation_code,
            telegram_id=333,
            device_fingerprint="redeem-device-1",
            platform="android",
            device_name="Phone 1",
        )
    )
    second = sub_service.redeem_code(
        RedeemActivationCodeRequest(
            code=payment.activation_code,
            telegram_id=333,
            device_fingerprint="redeem-device-2",
            platform="android",
            device_name="Phone 2",
        )
    )

    assert first.valid is True
    assert second.valid is True
    assert first.subscription_id == second.subscription_id

    code_row = db_session.scalar(
        select(ActivationCode).where(ActivationCode.subscription_id == payment.subscription_id)
    )
    assert code_row is not None
    assert code_row.first_redeemed_at is not None


def test_redeem_invalid_code_returns_401(db_session) -> None:
    sub_service = SubscriptionService(db_session)
    with pytest.raises(HTTPException) as exc:
        sub_service.redeem_code(
            RedeemActivationCodeRequest(
                code="INVALIDCODE12",
                telegram_id=444,
                device_fingerprint="dev-invalid-1",
                platform="android",
                device_name="Broken",
            )
        )
    assert exc.value.status_code == 401
