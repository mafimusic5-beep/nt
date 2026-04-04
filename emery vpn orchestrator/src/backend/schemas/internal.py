from pydantic import BaseModel, Field


class CreateOrderRequest(BaseModel):
    telegram_id: int
    plan_code: str = Field(pattern=r"^warmup_(1m|3m|6m|12m)$")


class CreateOrderResponse(BaseModel):
    order_id: int
    amount_rub: int
    currency: str
    status: str


class ConfirmPaymentRequest(BaseModel):
    order_id: int
    provider_payment_id: str
    idempotency_key: str
    paid: bool = True
    target_code: str | None = None
    issue_new_code: bool = False


class ConfirmPaymentResponse(BaseModel):
    payment_id: int
    status: str
    activation_code: str
    subscription_id: int
