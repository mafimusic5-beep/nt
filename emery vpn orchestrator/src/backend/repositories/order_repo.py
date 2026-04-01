from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from src.backend.repositories.base import BaseRepository
from src.common.models import ActivationCode, Order, Payment, Plan, Subscription


class OrderRepository(BaseRepository):
    def get_plan(self, plan_code: str) -> Plan | None:
        return self.db.scalar(select(Plan).where(Plan.code == plan_code, Plan.is_active.is_(True)))

    def create_order(self, user_id: int, plan: Plan) -> Order:
        order = Order(user_id=user_id, plan_id=plan.id, amount_rub=plan.price_rub, currency="RUB", status="pending")
        self.db.add(order)
        self.db.flush()
        return order

    def get_order(self, order_id: int) -> Order | None:
        return self.db.get(Order, order_id)

    def get_plan_by_id(self, plan_id: int) -> Plan | None:
        return self.db.get(Plan, plan_id)

    def get_payment_by_idempotency(self, idempotency_key: str) -> Payment | None:
        return self.db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))

    def create_payment(
        self,
        order_id: int,
        provider_payment_id: str,
        idempotency_key: str,
        months: int,
        amount_minor: int,
        status: str,
    ) -> Payment:
        payment = Payment(
            order_id=order_id,
            provider_payment_id=provider_payment_id,
            idempotency_key=idempotency_key,
            months=months,
            amount_minor=amount_minor,
            status=status,
            paid_at=datetime.now(timezone.utc) if status == "paid" else None,
        )
        self.db.add(payment)
        self.db.flush()
        return payment

    def create_or_extend_subscription(self, user_id: int, months: int, max_devices: int, region_code: str) -> Subscription:
        now = datetime.now(timezone.utc)
        current = self.db.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.ends_at > now,
            )
        )
        if current:
            current.ends_at = current.ends_at + relativedelta(months=months)
            return current
        sub = Subscription(
            user_id=user_id,
            plan_code="warmup",
            region_code=region_code,
            status="active",
            devices_limit=max_devices,
            starts_at=now,
            ends_at=now + relativedelta(months=months),
        )
        self.db.add(sub)
        self.db.flush()
        return sub

    def create_activation_code(self, user_id: int, subscription_id: int, code_hash: str) -> ActivationCode:
        code = ActivationCode(user_id=user_id, subscription_id=subscription_id, code_hash=code_hash, status="active")
        self.db.add(code)
        self.db.flush()
        return code
