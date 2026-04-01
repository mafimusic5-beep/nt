import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.backend.repositories.admin_repo import AdminRepository
from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.order_repo import OrderRepository
from src.backend.repositories.subscription_repo import SubscriptionRepository
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.schemas.internal import ConfirmPaymentRequest, ConfirmPaymentResponse, CreateOrderRequest, CreateOrderResponse
from src.backend.utils.debug_log import agent_log
from src.backend.utils.security import generate_activation_code, hash_activation_code, mask_secret
from src.common.config import settings

logger = logging.getLogger(__name__)


class OrderService:
    def __init__(self, db: Session):
        self.db = db
        self.order_repo = OrderRepository(db)
        self.sub_repo = SubscriptionRepository(db)
        self.admin_repo = AdminRepository(db)
        self.audit_repo = AuditRepository(db)
        self.node_orchestrator = NodeOrchestrationService(db)

    def _ensure_firstvds_allocation(self) -> dict:
        node = self.node_orchestrator.choose_best_moscow_node()
        if node:
            logger.info("existing active node %s found; skip auto-provision", node.id)
            return {"status": "skipped_existing_node", "node_id": node.id}

        auto_name = f"auto-node-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        node = self.admin_repo.create_node(
            name=auto_name,
            region_code="moscow",
            endpoint="",
            config_payload="",
            status="draft",
            health_status="unknown",
            load_score=1000,
            priority=0,
            capacity_clients=100,
            bandwidth_limit_mbps=1000,
            current_clients=0,
            per_device_speed_limit_mbps=100,
            firstvds_vps_id="",
            ssh_key_fingerprint="",
            ssh_key_status="missing",
        )
        logger.info("created draft node %s (%s) for auto-provision", node.id, node.name)
        provision = self.node_orchestrator.provision_node(node.id)
        logger.info("auto-provision node %s result=%s", node.id, provision.get("status"))
        return {"status": "auto_provision_attempted", "node_id": node.id, "provision": provision}

    def create_order(self, req: CreateOrderRequest) -> CreateOrderResponse:
        user = self.sub_repo.get_or_create_user(req.telegram_id)
        plan = self.order_repo.get_plan(req.plan_code)
        if not plan:
            raise HTTPException(status_code=400, detail="invalid_plan")
        order = self.order_repo.create_order(user.id, plan)
        self.audit_repo.write("internal", "system", "order_created", "order", str(order.id), {"plan_code": req.plan_code})
        self.db.commit()
        return CreateOrderResponse(order_id=order.id, amount_rub=order.amount_rub, currency=order.currency, status=order.status)

    def confirm_payment(self, req: ConfirmPaymentRequest) -> ConfirmPaymentResponse:
        # #region agent log
        agent_log(
            hypothesis_id="H1",
            location="order_service.py:confirm_payment",
            message="confirm_payment_enter",
            data={
                "order_id": req.order_id,
                "paid": req.paid,
                "provider_payment_id_prefix": req.provider_payment_id[:8],
            },
        )
        # #endregion
        existing = self.order_repo.get_payment_by_idempotency(req.idempotency_key)
        if existing:
            order = self.order_repo.get_order(existing.order_id)
            if not order or not order.subscription_id:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            logger.info("idempotent payment replay: order=%s payment=%s", order.id, existing.id)
            return ConfirmPaymentResponse(
                payment_id=existing.id,
                status=existing.status,
                activation_code="already_issued",
                subscription_id=order.subscription_id,
            )

        order = self.order_repo.get_order(req.order_id)
        if not order:
            raise HTTPException(status_code=404, detail="order_not_found")
        plan = self.order_repo.get_plan_by_id(order.plan_id)
        if not plan:
            raise HTTPException(status_code=500, detail="plan_mismatch")
        if not req.paid:
            payment = self.order_repo.create_payment(order.id, req.provider_payment_id, req.idempotency_key, plan.duration_months, order.amount_rub * 100, "failed")
            self.audit_repo.write("internal", "system", "payment_failed", "order", str(order.id), {"payment_id": payment.id})
            self.db.commit()
            # #region agent log
            agent_log(
                hypothesis_id="H1",
                location="order_service.py:confirm_payment",
                message="confirm_payment_rejected_not_paid",
                data={"order_id": order.id, "payment_id": payment.id},
            )
            # #endregion
            raise HTTPException(status_code=402, detail="payment_not_confirmed")

        subscription = self.order_repo.create_or_extend_subscription(
            user_id=order.user_id,
            months=plan.duration_months,
            max_devices=settings.max_devices_per_subscription,
            region_code=settings.default_region_code,
        )
        order.subscription_id = subscription.id
        order.status = "paid"
        plain_code = generate_activation_code(12)
        code_hash = hash_activation_code(plain_code)
        self.order_repo.create_activation_code(order.user_id, subscription.id, code_hash)
        payment = self.order_repo.create_payment(
            order.id,
            req.provider_payment_id,
            req.idempotency_key,
            plan.duration_months,
            order.amount_rub * 100,
            "paid",
        )
        self.audit_repo.write("internal", "system", "payment_confirmed", "order", str(order.id), {"payment_id": payment.id})
        self.audit_repo.write("internal", "system", "activation_code_created", "subscription", str(subscription.id), {"code": mask_secret(plain_code)})
        allocation_result = self._ensure_firstvds_allocation()
        self.audit_repo.write(
            "internal",
            "system",
            "firstvds_auto_allocation_attempted",
            "subscription",
            str(subscription.id),
            allocation_result,
        )
        logger.info(
            "payment confirmed: order=%s sub=%s allocation=%s",
            order.id, subscription.id, allocation_result.get("status"),
        )
        self.db.commit()
        # #region agent log
        agent_log(
            hypothesis_id="H1",
            location="order_service.py:confirm_payment",
            message="confirm_payment_exit_success",
            data={
                "order_id": order.id,
                "subscription_id": subscription.id,
                "allocation_status": allocation_result.get("status"),
                "activation_code_issued": True,
            },
        )
        # #endregion
        return ConfirmPaymentResponse(
            payment_id=payment.id,
            status=payment.status,
            activation_code=plain_code,
            subscription_id=subscription.id,
        )
