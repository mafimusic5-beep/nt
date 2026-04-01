from dateutil.relativedelta import relativedelta

from src.backend.schemas.internal import ConfirmPaymentRequest, CreateOrderRequest
from src.backend.services.order_service import OrderService


def test_subscription_extension_adds_paid_period(db_session) -> None:
    service = OrderService(db_session)
    first = service.create_order(CreateOrderRequest(telegram_id=111, plan_code="warmup_3m"))
    first_pay = service.confirm_payment(
        ConfirmPaymentRequest(
            order_id=first.order_id,
            provider_payment_id="pay_1",
            idempotency_key="idem_1",
            paid=True,
        )
    )
    first_ends_at = service.order_repo.get_order(first.order_id).subscription.ends_at

    second = service.create_order(CreateOrderRequest(telegram_id=111, plan_code="warmup_6m"))
    service.confirm_payment(
        ConfirmPaymentRequest(
            order_id=second.order_id,
            provider_payment_id="pay_2",
            idempotency_key="idem_2",
            paid=True,
        )
    )

    sub = service.order_repo.get_order(second.order_id).subscription
    assert sub.id == first_pay.subscription_id
    assert sub.ends_at >= first_ends_at + relativedelta(months=6)
