import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.backend.repositories.admin_repo import AdminRepository
from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.order_repo import OrderRepository
from src.backend.repositories.subscription_repo import SubscriptionRepository
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.services.capacity_service import CapacityService
from src.backend.services.provisioning_guard_service import ProvisioningGuardService
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

    def ensure_capacity_allocation(self) -> dict:
        capacity = CapacityService(self.db)
        all_nodes = self.admin_repo.list_nodes()
        now = datetime.now(timezone.utc)
        retryable = sorted(
            (node for node in all_nodes if node.status in {"draft", "provision_failed"} and node.provisioning_lock_key),
            key=lambda node: node.updated_at,
        )
        if retryable:
            node = retryable[0]
            retry_guard = ProvisioningGuardService().evaluate(
                region_code=node.region_code,
                nodes=[candidate for candidate in all_nodes if candidate.id != node.id],
            )
            if not retry_guard.allowed:
                return {
                    "status": "blocked_by_guardrail",
                    "reason": retry_guard.reason,
                    "node_id": node.id,
                    "region_code": node.region_code,
                    "projected_monthly_cost_eur": retry_guard.projected_monthly_cost_eur,
                }
            updated_at = node.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            retry_after = max(int(settings.auto_provision_retry_seconds), 30)
            age_seconds = (now - updated_at.astimezone(timezone.utc)).total_seconds()
            if node.status == "provision_failed" and age_seconds < retry_after:
                return {
                    "status": "provision_retry_cooldown",
                    "node_id": node.id,
                    "region_code": node.region_code,
                    "retry_after_seconds": int(retry_after - age_seconds),
                }
            provision = self.node_orchestrator.provision_node(node.id)
            return {
                "status": "auto_provision_retried",
                "node_id": node.id,
                "region_code": node.region_code,
                "provision": provision,
            }

        target = capacity.region_requiring_scale()
        default_region = settings.default_region_code.strip().lower()
        has_default_region = any(node.region_code == default_region for node in all_nodes)
        if default_region and not has_default_region:
            # CapacityService can only group regions represented by a node.
            # New subscriptions still need their configured default region even
            # when another unrelated region already has healthy capacity.
            target_region = default_region
        elif target is None:
            if all_nodes:
                return {"status": "skipped_capacity_available"}
            target_region = default_region
        else:
            target_region = target.region_code

        guard = ProvisioningGuardService().evaluate(
            region_code=target_region,
            nodes=all_nodes,
        )
        if not guard.allowed:
            return {
                "status": "blocked_by_guardrail",
                "reason": guard.reason,
                "region_code": target_region,
                "projected_monthly_cost_eur": guard.projected_monthly_cost_eur,
            }

        auto_name = f"auto-node-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        node = self.admin_repo.create_node(
            name=auto_name,
            region_code=target_region,
            endpoint="",
            config_payload="",
            status="draft",
            health_status="unknown",
            load_score=1000,
            priority=0,
            capacity_clients=settings.pool_node_capacity_devices,
            bandwidth_limit_mbps=settings.pool_node_bandwidth_mbps,
            current_clients=0,
            per_device_speed_limit_mbps=settings.pool_per_device_speed_limit_mbps,
            firstvds_vps_id="",
            ssh_key_fingerprint="",
            ssh_key_status="missing",
            provider=settings.auto_provision_provider.strip().lower(),
        )
        node.provisioning_lock_key = target_region
        try:
            # Publish the durable regional lock before any provider call. A
            # second worker then either sees the pending node or loses the
            # unique-key race without spending money.
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return {
                "status": "blocked_by_guardrail",
                "reason": "region_provisioning_already_in_progress",
                "region_code": target_region,
            }
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
            max_devices=plan.devices_limit,
            region_code=settings.default_region_code,
            plan_code=plan.code,
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
        # Payment, subscription and the one-time plaintext code must survive an
        # unavailable infrastructure provider. Provisioning is a follow-up
        # transaction and can be reconciled independently.
        self.db.commit()
        try:
            allocation_result = self.ensure_capacity_allocation()
        except Exception as exc:  # noqa: BLE001
            logger.exception("capacity allocation failed after committed payment")
            self.db.rollback()
            allocation_result = {
                "status": "allocation_failed",
                "reason": type(exc).__name__,
            }
        self.audit_repo.write(
            "internal",
            "system",
            "capacity_auto_allocation_evaluated",
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
