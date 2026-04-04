$root = Resolve-Path (Join-Path $PSScriptRoot '..')

$adminSchemas = Join-Path $root 'emery vpn orchestrator/src/backend/schemas/admin.py'
$internalSchemas = Join-Path $root 'emery vpn orchestrator/src/backend/schemas/internal.py'
$adminService = Join-Path $root 'emery vpn orchestrator/src/backend/services/admin_service.py'
$apiRoutes = Join-Path $root 'emery vpn orchestrator/src/backend/api/routes.py'
$orderService = Join-Path $root 'emery vpn orchestrator/src/backend/services/order_service.py'
$keyboards = Join-Path $root 'emery vpn orchestrator/src/bot/ui/keyboards.py'
$backendClient = Join-Path $root 'emery vpn orchestrator/src/bot/api/backend_client.py'
$adminHandler = Join-Path $root 'emery vpn orchestrator/src/bot/handlers/admin.py'
$startHandler = Join-Path $root 'emery vpn orchestrator/src/bot/handlers/start.py'

@'
from datetime import datetime

from pydantic import BaseModel


class GrantSubscriptionRequest(BaseModel):
    telegram_id: int
    months: int
    region_code: str = "moscow"


class GrantSubscriptionResponse(BaseModel):
    subscription_id: int
    ends_at: datetime


class VpnNodeUpsertRequest(BaseModel):
    name: str
    region_code: str = "moscow"
    endpoint: str
    config_payload: str
    status: str = "active"
    health_status: str = "unknown"
    load_score: int = 1000
    priority: int = 0
    capacity_clients: int = 100
    bandwidth_limit_mbps: int = 1000
    current_clients: int = 0
    per_device_speed_limit_mbps: int = 100
    firstvds_vps_id: str = ""
    ssh_key_fingerprint: str = ""
    ssh_key_status: str = "missing"


class VpnNodeResponse(BaseModel):
    id: int
    name: str
    region_code: str
    endpoint: str
    status: str
    health_status: str
    load_score: int
    priority: int
    capacity_clients: int
    current_clients: int
    bandwidth_limit_mbps: int
    per_device_speed_limit_mbps: int
    ssh_key_fingerprint: str
    ssh_key_status: str
    has_valid_config: bool = False


class AdminStatsResponse(BaseModel):
    users: int
    subscriptions: int
    active_devices: int
    orders: int
    payments: int
    codes: int


class ManualCodeResponse(BaseModel):
    activation_code: str
    subscription_id: int


class ProblemActivationResponse(BaseModel):
    created_at: datetime
    actor_id: str
    action: str
    entity_id: str
    details: str


class NodeActionResponse(BaseModel):
    node_id: int
    status: str
    detail: str | None = None
    returncode: int | None = None


class BestNodeResponse(BaseModel):
    id: int
    name: str
    region_code: str
    status: str
    health_status: str
    load_score: int
    priority: int
    capacity_clients: int
    current_clients: int


class HealthcheckRunResponse(BaseModel):
    checked: int
    results: list[dict]


class ActivationCodeListItemResponse(BaseModel):
    id: int
    code_hash: str
    status: str
    telegram_id: int
    subscription_id: int
    subscription_status: str
    region_code: str
    created_at: datetime
    first_redeemed_at: datetime | None
    subscription_ends_at: datetime


class ActivationCodeInfoResponse(BaseModel):
    id: int
    code_hash: str
    status: str
    user_id: int
    telegram_id: int
    subscription_id: int
    subscription_status: str
    region_code: str
    created_at: datetime
    first_redeemed_at: datetime | None
    subscription_ends_at: datetime


class ActivationCodeDeleteResponse(BaseModel):
    id: int
    code_hash: str
    status: str
    deleted: bool
'@ | Set-Content $adminSchemas -Encoding UTF8

@'
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
'@ | Set-Content $internalSchemas -Encoding UTF8

@'
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.backend.repositories.admin_repo import AdminRepository
from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.order_repo import OrderRepository
from src.backend.repositories.subscription_repo import SubscriptionRepository
from src.backend.schemas.admin import (
    ActivationCodeDeleteResponse,
    ActivationCodeInfoResponse,
    ActivationCodeListItemResponse,
    GrantSubscriptionRequest,
    GrantSubscriptionResponse,
    VpnNodeResponse,
    VpnNodeUpsertRequest,
)
from src.backend.services.node_adapters import FirstVdsBillManagerProvisioningService
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.utils.security import generate_activation_code, hash_activation_code
from src.common.config import settings
from src.common.models import ActivationCode, User

logger = logging.getLogger(__name__)


class AdminService:
    def __init__(self, db: Session):
        self.db = db
        self.admin_repo = AdminRepository(db)
        self.sub_repo = SubscriptionRepository(db)
        self.order_repo = OrderRepository(db)
        self.audit_repo = AuditRepository(db)
        self.node_orchestrator = NodeOrchestrationService(db)

    def grant_subscription(self, req: GrantSubscriptionRequest) -> GrantSubscriptionResponse:
        if req.months <= 0:
            raise HTTPException(status_code=400, detail="invalid_months")
        user = self.sub_repo.get_or_create_user(req.telegram_id)
        sub = self.order_repo.create_or_extend_subscription(user.id, req.months, settings.max_devices_per_subscription, req.region_code)
        self.audit_repo.write("admin", "api", "grant_subscription", "subscription", str(sub.id), {"months": req.months})
        self.db.commit()
        return GrantSubscriptionResponse(subscription_id=sub.id, ends_at=sub.ends_at)

    @staticmethod
    def _node_response(n) -> VpnNodeResponse:
        return VpnNodeResponse(
            id=n.id,
            name=n.name,
            region_code=n.region_code,
            endpoint=n.endpoint,
            status=n.status,
            health_status=n.health_status,
            load_score=n.load_score,
            priority=n.priority,
            capacity_clients=n.capacity_clients,
            current_clients=n.current_clients,
            bandwidth_limit_mbps=n.bandwidth_limit_mbps,
            per_device_speed_limit_mbps=n.per_device_speed_limit_mbps,
            ssh_key_fingerprint=n.ssh_key_fingerprint,
            ssh_key_status=n.ssh_key_status,
            has_valid_config=FirstVdsBillManagerProvisioningService.is_config_payload_valid(n.config_payload or ""),
        )

    def create_node(self, req: VpnNodeUpsertRequest) -> VpnNodeResponse:
        if req.region_code != "moscow":
            raise HTTPException(status_code=400, detail="only_moscow_region_supported")
        node = self.admin_repo.create_node(
            req.name,
            req.region_code,
            req.endpoint,
            req.config_payload,
            req.status,
            req.health_status,
            req.load_score,
            req.priority,
            req.capacity_clients,
            req.bandwidth_limit_mbps,
            req.current_clients,
            req.per_device_speed_limit_mbps,
            req.firstvds_vps_id,
            req.ssh_key_fingerprint,
            req.ssh_key_status,
        )
        self.audit_repo.write("admin", "api", "create_node", "vpn_node", str(node.id), {"region": node.region_code})
        self.db.commit()
        return self._node_response(node)

    def list_nodes(self) -> list[VpnNodeResponse]:
        nodes = self.admin_repo.list_nodes()
        return [self._node_response(n) for n in nodes]

    def stats(self) -> dict[str, int]:
        return self.admin_repo.stats()

    def generate_code(self, telegram_id: int) -> dict:
        user = self.sub_repo.get_or_create_user(telegram_id)
        sub = self.sub_repo.get_active_subscription(user.id)
        if not sub:
            raise HTTPException(status_code=404, detail="active_subscription_not_found")
        plain = generate_activation_code(12)
        self.order_repo.create_activation_code(user.id, sub.id, hash_activation_code(plain))
        self.audit_repo.write("admin", "api", "manual_activation_code_generated", "subscription", str(sub.id))
        self.db.commit()
        return {"activation_code": plain, "subscription_id": sub.id}

    def problem_activations(self) -> list[dict]:
        rows = self.admin_repo.list_problem_activations()
        return [
            {
                "created_at": r.created_at,
                "actor_id": r.actor_id,
                "action": r.action,
                "entity_id": r.entity_id,
                "details": r.details,
            }
            for r in rows
        ]

    def list_codes(self, limit: int = 10, offset: int = 0) -> list[ActivationCodeListItemResponse]:
        limit = max(1, min(limit, 50))
        offset = max(0, offset)
        codes = self.db.scalars(
            select(ActivationCode)
            .order_by(ActivationCode.created_at.desc(), ActivationCode.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        items: list[ActivationCodeListItemResponse] = []
        for code in codes:
            user = self.db.get(User, code.user_id)
            sub = self.sub_repo.get_subscription(code.subscription_id)
            if not user or not sub:
                continue
            items.append(
                ActivationCodeListItemResponse(
                    id=code.id,
                    code_hash=code.code_hash,
                    status=code.status,
                    telegram_id=user.telegram_id,
                    subscription_id=code.subscription_id,
                    subscription_status=sub.status,
                    region_code=sub.region_code,
                    created_at=code.created_at,
                    first_redeemed_at=code.first_redeemed_at,
                    subscription_ends_at=sub.ends_at,
                )
            )
        return items

    def _build_code_info(self, code: ActivationCode) -> ActivationCodeInfoResponse:
        user = self.db.get(User, code.user_id)
        sub = self.sub_repo.get_subscription(code.subscription_id)
        if not user or not sub:
            raise HTTPException(status_code=404, detail="activation_code_relations_not_found")
        return ActivationCodeInfoResponse(
            id=code.id,
            code_hash=code.code_hash,
            status=code.status,
            user_id=code.user_id,
            telegram_id=user.telegram_id,
            subscription_id=code.subscription_id,
            subscription_status=sub.status,
            region_code=sub.region_code,
            created_at=code.created_at,
            first_redeemed_at=code.first_redeemed_at,
            subscription_ends_at=sub.ends_at,
        )

    def get_code_info(self, plain_code: str) -> ActivationCodeInfoResponse:
        normalized = plain_code.strip().upper()
        if not normalized:
            raise HTTPException(status_code=400, detail="activation_code_required")
        code_hash = hash_activation_code(normalized)
        code = self.admin_repo.get_activation_code_by_hash(code_hash)
        if not code:
            raise HTTPException(status_code=404, detail="activation_code_not_found")
        return self._build_code_info(code)

    def get_code_info_by_id(self, code_id: int) -> ActivationCodeInfoResponse:
        code = self.db.get(ActivationCode, code_id)
        if not code:
            raise HTTPException(status_code=404, detail="activation_code_not_found")
        return self._build_code_info(code)

    def delete_code(self, plain_code: str) -> ActivationCodeDeleteResponse:
        normalized = plain_code.strip().upper()
        if not normalized:
            raise HTTPException(status_code=400, detail="activation_code_required")
        code_hash = hash_activation_code(normalized)
        code = self.admin_repo.get_activation_code_by_hash(code_hash)
        if not code:
            raise HTTPException(status_code=404, detail="activation_code_not_found")
        return self.delete_code_by_id(code.id)

    def delete_code_by_id(self, code_id: int) -> ActivationCodeDeleteResponse:
        code = self.db.get(ActivationCode, code_id)
        if not code:
            raise HTTPException(status_code=404, detail="activation_code_not_found")
        if code.status != "active":
            return ActivationCodeDeleteResponse(id=code.id, code_hash=code.code_hash, status=code.status, deleted=False)
        self.admin_repo.revoke_activation_code(code)
        self.audit_repo.write(
            "admin",
            "api",
            "activation_code_deleted",
            "activation_code",
            str(code.id),
            {"subscription_id": code.subscription_id},
        )
        self.db.commit()
        return ActivationCodeDeleteResponse(id=code.id, code_hash=code.code_hash, status=code.status, deleted=True)

    def generate_code_for_code_id(self, code_id: int) -> dict:
        code = self.db.get(ActivationCode, code_id)
        if not code:
            raise HTTPException(status_code=404, detail="activation_code_not_found")
        sub = self.sub_repo.get_subscription(code.subscription_id)
        if not sub:
            raise HTTPException(status_code=404, detail="subscription_not_found")
        plain = generate_activation_code(12)
        self.order_repo.create_activation_code(code.user_id, sub.id, hash_activation_code(plain))
        self.audit_repo.write(
            "admin",
            "api",
            "manual_activation_code_generated_for_existing_subscription",
            "subscription",
            str(sub.id),
            {"source_code_id": code.id},
        )
        self.db.commit()
        return {"activation_code": plain, "subscription_id": sub.id}

    def best_moscow_node(self) -> dict:
        node = self.node_orchestrator.choose_best_moscow_node()
        if not node:
            raise HTTPException(status_code=404, detail="no_suitable_moscow_node")
        return {
            "id": node.id,
            "name": node.name,
            "region_code": node.region_code,
            "status": node.status,
            "health_status": node.health_status,
            "load_score": node.load_score,
            "priority": node.priority,
            "capacity_clients": node.capacity_clients,
            "current_clients": node.current_clients,
        }

    def provision_node(self, node_id: int) -> dict:
        return self.node_orchestrator.provision_node(node_id)

    def deprovision_node(self, node_id: int) -> dict:
        return self.node_orchestrator.deprovision_node(node_id)

    def run_healthcheck(self) -> dict:
        return self.node_orchestrator.run_healthcheck()
'@ | Set-Content $adminService -Encoding UTF8

@'
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.backend.deps.auth import require_admin_api_key, require_internal_api_key
from src.backend.deps.db import get_db
from src.backend.schemas.admin import (
    ActivationCodeDeleteResponse,
    ActivationCodeInfoResponse,
    ActivationCodeListItemResponse,
    AdminStatsResponse,
    BestNodeResponse,
    GrantSubscriptionRequest,
    GrantSubscriptionResponse,
    HealthcheckRunResponse,
    ManualCodeResponse,
    NodeActionResponse,
    ProblemActivationResponse,
    VpnNodeResponse,
    VpnNodeUpsertRequest,
)
from src.backend.schemas.internal import ConfirmPaymentRequest, ConfirmPaymentResponse, CreateOrderRequest, CreateOrderResponse
from src.backend.schemas.subscription import (
    HeartbeatRequest,
    RedeemActivationCodeRequest,
    RedeemActivationCodeResponse,
    RegisterDeviceRequest,
    RegisterDeviceResponse,
    SubscriptionStatusResponse,
    UnbindDeviceRequest,
    UserCodeResponse,
    UserDeviceResponse,
    VpnConfigResponse,
    VpnConnectRequest,
    VpnConnectResponse,
    VpnServerItemResponse,
)
from src.backend.services.admin_service import AdminService
from src.backend.services.order_service import OrderService
from src.backend.services.subscription_service import SubscriptionService
from src.backend.utils.debug_log import agent_log

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.post("/redeem", response_model=RedeemActivationCodeResponse)
def redeem_activation_code(payload: RedeemActivationCodeRequest, db: Session = Depends(get_db)):
    return SubscriptionService(db).redeem_code(payload)


@router.get("/subscription/status", response_model=SubscriptionStatusResponse)
def get_subscription_status(telegram_id: int, db: Session = Depends(get_db)):
    return SubscriptionService(db).get_status(telegram_id)


@router.post("/device/register", response_model=RegisterDeviceResponse)
def register_device(payload: RegisterDeviceRequest, db: Session = Depends(get_db)):
    return SubscriptionService(db).register_device(payload)


@router.post("/device/heartbeat")
def heartbeat_device(payload: HeartbeatRequest, db: Session = Depends(get_db)):
    SubscriptionService(db).heartbeat(payload)
    return {"status": "ok"}


@router.post("/device/unbind")
def unbind_device(payload: UnbindDeviceRequest, db: Session = Depends(get_db)):
    SubscriptionService(db).unbind(payload.telegram_id, payload.device_fingerprint)
    return {"status": "ok"}


@router.get("/vpn/config", response_model=VpnConfigResponse)
def get_vpn_config(telegram_id: int, db: Session = Depends(get_db)):
    agent_log(
        hypothesis_id="H2",
        location="routes.py:get_vpn_config",
        message="vpn_config_requested",
        data={"telegram_id": telegram_id},
    )
    return SubscriptionService(db).get_vpn_config(telegram_id)


@router.get("/vpn/servers", response_model=list[VpnServerItemResponse])
def get_vpn_servers(db: Session = Depends(get_db)):
    return SubscriptionService(db).list_vpn_servers()


@router.post("/vpn/connect", response_model=VpnConnectResponse)
def connect_vpn_server(payload: VpnConnectRequest, db: Session = Depends(get_db)):
    return SubscriptionService(db).connect_to_server(payload.access_key, payload.server_id)


@router.get("/user/devices", response_model=list[UserDeviceResponse])
def user_devices(telegram_id: int, db: Session = Depends(get_db)):
    return SubscriptionService(db).list_user_devices(telegram_id)


@router.get("/user/codes", response_model=list[UserCodeResponse])
def user_codes(telegram_id: int, db: Session = Depends(get_db)):
    return SubscriptionService(db).list_user_codes(telegram_id)


@router.post("/internal/orders", response_model=CreateOrderResponse, dependencies=[Depends(require_internal_api_key)])
def internal_create_order(payload: CreateOrderRequest, db: Session = Depends(get_db)):
    return OrderService(db).create_order(payload)


@router.post(
    "/internal/payments/confirm",
    response_model=ConfirmPaymentResponse,
    dependencies=[Depends(require_internal_api_key)],
)
def internal_confirm_payment(payload: ConfirmPaymentRequest, db: Session = Depends(get_db)):
    agent_log(
        hypothesis_id="H1",
        location="routes.py:internal_confirm_payment",
        message="internal_confirm_payment_called",
        data={
            "order_id": payload.order_id,
            "paid": payload.paid,
            "provider_payment_id_prefix": payload.provider_payment_id[:8],
            "has_target_code": bool(payload.target_code),
            "issue_new_code": payload.issue_new_code,
        },
    )
    return OrderService(db).confirm_payment(payload)


@router.post("/admin/subscription/grant", response_model=GrantSubscriptionResponse, dependencies=[Depends(require_admin_api_key)])
def admin_grant_subscription(payload: GrantSubscriptionRequest, db: Session = Depends(get_db)):
    return AdminService(db).grant_subscription(payload)


@router.get("/admin/nodes", response_model=list[VpnNodeResponse], dependencies=[Depends(require_admin_api_key)])
def admin_list_nodes(db: Session = Depends(get_db)):
    return AdminService(db).list_nodes()


@router.post("/admin/nodes", response_model=VpnNodeResponse, dependencies=[Depends(require_admin_api_key)])
def admin_create_node(payload: VpnNodeUpsertRequest, db: Session = Depends(get_db)):
    return AdminService(db).create_node(payload)


@router.get("/admin/stats", response_model=AdminStatsResponse, dependencies=[Depends(require_admin_api_key)])
def admin_stats(db: Session = Depends(get_db)):
    return AdminService(db).stats()


@router.post("/admin/codes/generate", response_model=ManualCodeResponse, dependencies=[Depends(require_admin_api_key)])
def admin_generate_code(telegram_id: int, db: Session = Depends(get_db)):
    return AdminService(db).generate_code(telegram_id)


@router.get("/admin/codes", response_model=list[ActivationCodeListItemResponse], dependencies=[Depends(require_admin_api_key)])
def admin_list_codes(limit: int = 10, offset: int = 0, db: Session = Depends(get_db)):
    return AdminService(db).list_codes(limit=limit, offset=offset)


@router.get("/admin/codes/info", response_model=ActivationCodeInfoResponse, dependencies=[Depends(require_admin_api_key)])
def admin_code_info(code: str, db: Session = Depends(get_db)):
    return AdminService(db).get_code_info(code)


@router.get("/admin/codes/{code_id}", response_model=ActivationCodeInfoResponse, dependencies=[Depends(require_admin_api_key)])
def admin_code_info_by_id(code_id: int, db: Session = Depends(get_db)):
    return AdminService(db).get_code_info_by_id(code_id)


@router.delete("/admin/codes", response_model=ActivationCodeDeleteResponse, dependencies=[Depends(require_admin_api_key)])
def admin_delete_code(code: str, db: Session = Depends(get_db)):
    return AdminService(db).delete_code(code)


@router.delete("/admin/codes/{code_id}", response_model=ActivationCodeDeleteResponse, dependencies=[Depends(require_admin_api_key)])
def admin_delete_code_by_id(code_id: int, db: Session = Depends(get_db)):
    return AdminService(db).delete_code_by_id(code_id)


@router.post("/admin/codes/{code_id}/generate", response_model=ManualCodeResponse, dependencies=[Depends(require_admin_api_key)])
def admin_generate_code_for_existing_key(code_id: int, db: Session = Depends(get_db)):
    return AdminService(db).generate_code_for_code_id(code_id)


@router.get(
    "/admin/activations/problems",
    response_model=list[ProblemActivationResponse],
    dependencies=[Depends(require_admin_api_key)],
)
def admin_problem_activations(db: Session = Depends(get_db)):
    return AdminService(db).problem_activations()


@router.get("/admin/nodes/best-moscow", response_model=BestNodeResponse, dependencies=[Depends(require_admin_api_key)])
def admin_best_moscow_node(db: Session = Depends(get_db)):
    return AdminService(db).best_moscow_node()


@router.post(
    "/admin/nodes/{node_id}/provision",
    response_model=NodeActionResponse,
    dependencies=[Depends(require_admin_api_key)],
)
def admin_provision_node(node_id: int, db: Session = Depends(get_db)):
    return AdminService(db).provision_node(node_id)


@router.post(
    "/admin/nodes/{node_id}/deprovision",
    response_model=NodeActionResponse,
    dependencies=[Depends(require_admin_api_key)],
)
def admin_deprovision_node(node_id: int, db: Session = Depends(get_db)):
    return AdminService(db).deprovision_node(node_id)


@router.post(
    "/admin/nodes/healthcheck/run",
    response_model=HealthcheckRunResponse,
    dependencies=[Depends(require_admin_api_key)],
)
def admin_run_healthcheck(db: Session = Depends(get_db)):
    return AdminService(db).run_healthcheck()
'@ | Set-Content $apiRoutes -Encoding UTF8

@'
import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.backend.repositories.admin_repo import AdminRepository
from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.order_repo import OrderRepository
from src.backend.repositories.subscription_repo import SubscriptionRepository
from src.backend.schemas.internal import ConfirmPaymentRequest, ConfirmPaymentResponse, CreateOrderRequest, CreateOrderResponse
from src.backend.services.node_orchestration_service import NodeOrchestrationService
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
        agent_log(
            hypothesis_id="H1",
            location="order_service.py:confirm_payment",
            message="confirm_payment_enter",
            data={
                "order_id": req.order_id,
                "paid": req.paid,
                "provider_payment_id_prefix": req.provider_payment_id[:8],
                "has_target_code": bool(req.target_code),
                "issue_new_code": req.issue_new_code,
            },
        )
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
            agent_log(
                hypothesis_id="H1",
                location="order_service.py:confirm_payment",
                message="confirm_payment_rejected_not_paid",
                data={"order_id": order.id, "payment_id": payment.id},
            )
            raise HTTPException(status_code=402, detail="payment_not_confirmed")

        activation_code_to_return = None
        if req.target_code:
            normalized_target_code = req.target_code.strip().upper()
            code_hash = hash_activation_code(normalized_target_code)
            target_code = self.sub_repo.get_activation_code(code_hash)
            if not target_code:
                raise HTTPException(status_code=404, detail="activation_code_not_found")
            target_sub = self.sub_repo.get_subscription(target_code.subscription_id)
            if not target_sub:
                raise HTTPException(status_code=404, detail="subscription_not_found")
            subscription = self.order_repo.create_or_extend_subscription(
                user_id=target_sub.user_id,
                months=plan.duration_months,
                max_devices=settings.max_devices_per_subscription,
                region_code=target_sub.region_code,
            )
            order.subscription_id = subscription.id
            order.status = "paid"
            if req.issue_new_code:
                plain_code = generate_activation_code(12)
                code_hash = hash_activation_code(plain_code)
                self.order_repo.create_activation_code(target_sub.user_id, subscription.id, code_hash)
                activation_code_to_return = plain_code
                self.audit_repo.write(
                    "internal",
                    "system",
                    "activation_code_created_for_key_renewal",
                    "subscription",
                    str(subscription.id),
                    {"code": mask_secret(plain_code), "mode": "new_code"},
                )
            else:
                target_code.subscription_id = subscription.id
                target_code.status = "active"
                activation_code_to_return = normalized_target_code
                self.audit_repo.write(
                    "internal",
                    "system",
                    "activation_code_rebound_after_payment",
                    "subscription",
                    str(subscription.id),
                    {"existing_code_hash": target_code.code_hash},
                )
        else:
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
            activation_code_to_return = plain_code
            self.audit_repo.write("internal", "system", "activation_code_created", "subscription", str(subscription.id), {"code": mask_secret(plain_code)})

        payment = self.order_repo.create_payment(
            order.id,
            req.provider_payment_id,
            req.idempotency_key,
            plan.duration_months,
            order.amount_rub * 100,
            "paid",
        )
        self.audit_repo.write("internal", "system", "payment_confirmed", "order", str(order.id), {"payment_id": payment.id})
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
        agent_log(
            hypothesis_id="H1",
            location="order_service.py:confirm_payment",
            message="confirm_payment_exit_success",
            data={
                "order_id": order.id,
                "subscription_id": subscription.id,
                "allocation_status": allocation_result.get("status"),
                "activation_code_issued": True,
                "has_target_code": bool(req.target_code),
            },
        )
        return ConfirmPaymentResponse(
            payment_id=payment.id,
            status=payment.status,
            activation_code=activation_code_to_return or "—",
            subscription_id=subscription.id,
        )
'@ | Set-Content $orderService -Encoding UTF8

@'
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.common.config import settings


def main_menu_keyboard(telegram_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Купить подписку", callback_data="menu_buy")
    kb.button(text="Продлить по ключу", callback_data="menu_extend_key")
    kb.button(text="Мои подписки", callback_data="menu_my_sub")
    kb.button(text="Получить VPN-конфиг", callback_data="menu_vpn_config")
    kb.button(text="Мои устройства", callback_data="menu_my_devices")
    kb.button(text="Мои коды", callback_data="menu_my_codes")
    kb.button(text="Помощь", callback_data="menu_help")
    if telegram_id in settings.admin_id_list:
        kb.button(text="Админ", callback_data="menu_admin")
    kb.button(text="Поддержка", url=settings.support_url)
    kb.button(text="Канал", url=settings.channel_url)
    kb.adjust(1)
    return kb.as_markup()


def plans_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="1 месяц — 600 руб", callback_data="buy_warmup_1m")
    kb.button(text="3 месяца — 1500 руб", callback_data="buy_warmup_3m")
    kb.button(text="6 месяцев — 2700 руб", callback_data="buy_warmup_6m")
    kb.button(text="12 месяцев — 4800 руб", callback_data="buy_warmup_12m")
    kb.button(text="Назад в меню", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()


def renew_mode_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Продлить этот же ключ", callback_data="renewmode_keep")
    kb.button(text="Продлить и выдать новый ключ", callback_data="renewmode_new")
    kb.button(text="Назад в меню", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()


def renew_plans_keyboard(mode: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="1 месяц — 600 руб", callback_data=f"renewplan_{mode}_warmup_1m")
    kb.button(text="3 месяца — 1500 руб", callback_data=f"renewplan_{mode}_warmup_3m")
    kb.button(text="6 месяцев — 2700 руб", callback_data=f"renewplan_{mode}_warmup_6m")
    kb.button(text="12 месяцев — 4800 руб", callback_data=f"renewplan_{mode}_warmup_12m")
    kb.button(text="Назад в меню", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()


def pay_keyboard(order_id: int, plan_code: str, target_code: str | None = None, issue_new_code: bool = False) -> InlineKeyboardMarkup:
    if target_code:
        mode = "new" if issue_new_code else "keep"
        callback_data = f"pay|{order_id}|{plan_code}|renew|{mode}|{target_code.upper()}"
    else:
        callback_data = f"pay|{order_id}|{plan_code}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я оплатил", callback_data=callback_data)],
            [InlineKeyboardButton(text="Назад в меню", callback_data="menu_back")],
        ]
    )


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Все ключи", callback_data="admin_keys_0")
    kb.button(text="Статистика", callback_data="admin_stats")
    kb.button(text="Список узлов", callback_data="admin_nodes")
    kb.button(text="Выдать себе +1 месяц", callback_data="admin_grant_self")
    kb.button(text="Сгенерировать код себе", callback_data="admin_code_self")
    kb.button(text="Проблемные активации", callback_data="admin_problem_activations")
    kb.button(text="Назад в меню", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()


def admin_keys_keyboard(items: list[dict], page: int, has_next: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for item in items:
        short_hash = (item.get("code_hash") or "")[:8]
        status = item.get("status") or "-"
        tg = item.get("telegram_id") or "-"
        kb.button(text=f"#{item['id']} tg:{tg} {status} {short_hash}", callback_data=f"admin_key_{item['id']}_{page}")
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_keys_{page - 1}"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_keys_{page + 1}"))
    if nav_row:
        kb.row(*nav_row)
    kb.row(InlineKeyboardButton(text="Назад", callback_data="menu_admin"))
    kb.adjust(1)
    return kb.as_markup()


def admin_key_card_keyboard(code_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Удалить", callback_data=f"admin_key_delete_{code_id}_{page}")],
            [InlineKeyboardButton(text="Сгенерировать новый код", callback_data=f"admin_key_generate_{code_id}_{page}")],
            [InlineKeyboardButton(text="Назад к списку", callback_data=f"admin_keys_{page}")],
        ]
    )
'@ | Set-Content $keyboards -Encoding UTF8

@'
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from src.common.config import settings


class BackendClientError(Exception):
    def __init__(self, detail: str, status_code: int = 500):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(slots=True)
class BackendClient:
    base_url: str = settings.backend_base_url.rstrip("/")
    internal_api_key: str = settings.internal_api_key
    admin_api_key: str = settings.admin_api_key

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        req_headers = headers or {}
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                response = await client.request(method, url, json=json_data, params=params, headers=req_headers)
            except httpx.HTTPError as exc:
                raise BackendClientError("backend_unreachable", 503) from exc
        payload = {}
        if response.content:
            try:
                payload = response.json()
            except ValueError:
                payload = {"detail": "invalid_backend_payload"}
        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else "backend_error"
            raise BackendClientError(str(detail), response.status_code)
        return payload

    async def get_subscription_status(self, telegram_id: int) -> dict:
        return await self._request("GET", "/api/v1/subscription/status", params={"telegram_id": telegram_id})

    async def get_user_devices(self, telegram_id: int) -> list[dict]:
        return await self._request("GET", "/api/v1/user/devices", params={"telegram_id": telegram_id})

    async def get_vpn_config(self, telegram_id: int) -> dict:
        return await self._request("GET", "/api/v1/vpn/config", params={"telegram_id": telegram_id})

    async def get_user_codes(self, telegram_id: int) -> list[dict]:
        return await self._request("GET", "/api/v1/user/codes", params={"telegram_id": telegram_id})

    async def create_order(self, telegram_id: int, plan_code: str) -> dict:
        return await self._request(
            "POST",
            "/api/v1/internal/orders",
            json_data={"telegram_id": telegram_id, "plan_code": plan_code},
            headers={"X-Internal-Api-Key": self.internal_api_key},
        )

    async def confirm_payment(
        self,
        order_id: int,
        provider_payment_id: str,
        idempotency_key: str,
        target_code: str | None = None,
        issue_new_code: bool = False,
    ) -> dict:
        payload = {
            "order_id": order_id,
            "provider_payment_id": provider_payment_id,
            "idempotency_key": idempotency_key,
            "paid": True,
        }
        if target_code:
            payload["target_code"] = target_code
            payload["issue_new_code"] = issue_new_code
        return await self._request(
            "POST",
            "/api/v1/internal/payments/confirm",
            json_data=payload,
            headers={"X-Internal-Api-Key": self.internal_api_key},
        )

    async def admin_stats(self) -> dict:
        return await self._request("GET", "/api/v1/admin/stats", headers={"X-Admin-Api-Key": self.admin_api_key})

    async def admin_nodes(self) -> list[dict]:
        return await self._request("GET", "/api/v1/admin/nodes", headers={"X-Admin-Api-Key": self.admin_api_key})

    async def admin_grant_subscription(self, telegram_id: int, months: int) -> dict:
        return await self._request(
            "POST",
            "/api/v1/admin/subscription/grant",
            json_data={"telegram_id": telegram_id, "months": months, "region_code": settings.default_region_code},
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_generate_code(self, telegram_id: int) -> dict:
        return await self._request(
            "POST",
            "/api/v1/admin/codes/generate",
            params={"telegram_id": telegram_id},
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_problem_activations(self) -> list[dict]:
        return await self._request(
            "GET",
            "/api/v1/admin/activations/problems",
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_code_info(self, code: str) -> dict:
        return await self._request(
            "GET",
            "/api/v1/admin/codes/info",
            params={"code": code},
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_delete_code(self, code: str) -> dict:
        return await self._request(
            "DELETE",
            "/api/v1/admin/codes",
            params={"code": code},
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_list_codes(self, limit: int = 10, offset: int = 0) -> list[dict]:
        return await self._request(
            "GET",
            "/api/v1/admin/codes",
            params={"limit": limit, "offset": offset},
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_get_code(self, code_id: int) -> dict:
        return await self._request(
            "GET",
            f"/api/v1/admin/codes/{code_id}",
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_delete_code_by_id(self, code_id: int) -> dict:
        return await self._request(
            "DELETE",
            f"/api/v1/admin/codes/{code_id}",
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_generate_code_for_key(self, code_id: int) -> dict:
        return await self._request(
            "POST",
            f"/api/v1/admin/codes/{code_id}/generate",
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )
'@ | Set-Content $backendClient -Encoding UTF8

@'
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import CallbackQuery, Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import (
    admin_key_card_keyboard,
    admin_keys_keyboard,
    admin_menu_keyboard,
    main_menu_keyboard,
)
from src.bot.utils.access import is_admin
from src.bot.utils.formatters import format_dt

logger = logging.getLogger(__name__)

router = Router(name="admin_menu")
client = BackendClient()
_PAGE_SIZE = 10


def _code_card_text(info: dict) -> str:
    return (
        "Информация по ключу:\n"
        f"ID: {info.get('id')}\n"
        f"Hash: <code>{(info.get('code_hash') or '')[:16]}</code>\n"
        f"Статус: {info.get('status')}\n"
        f"Telegram ID: {info.get('telegram_id')}\n"
        f"User ID: {info.get('user_id')}\n"
        f"Subscription ID: {info.get('subscription_id')}\n"
        f"Статус подписки: {info.get('subscription_status')}\n"
        f"Регион: {info.get('region_code')}\n"
        f"Создан: {format_dt(info.get('created_at'))}\n"
        f"Первое использование: {format_dt(info.get('first_redeemed_at'))}\n"
        f"Действует до: {format_dt(info.get('subscription_ends_at'))}"
    )


async def _show_admin_panel(message: Message) -> None:
    await message.answer(
        "Админ-панель\n\n"
        "Команды:\n"
        "- /keyinfo <код> — информация по ключу\n"
        "- /keydelete <код> — удалить (деактивировать) ключ",
        reply_markup=admin_menu_keyboard(),
    )


async def _render_keys_list(message: Message, page: int) -> None:
    offset = page * _PAGE_SIZE
    rows = await client.admin_list_codes(limit=_PAGE_SIZE, offset=offset)
    has_next = len(rows) == _PAGE_SIZE
    if not rows and page == 0:
        await message.edit_text("Ключей пока нет.", reply_markup=admin_menu_keyboard())
        return
    if not rows:
        await message.edit_text("Эта страница пуста.", reply_markup=admin_menu_keyboard())
        return
    lines = ["Все ключи:"]
    for row in rows:
        lines.append(
            f"#{row.get('id')} | tg:{row.get('telegram_id')} | {row.get('status')} | до {format_dt(row.get('subscription_ends_at'))}"
        )
    await message.edit_text(
        "\n".join(lines),
        reply_markup=admin_keys_keyboard(rows, page, has_next),
    )


@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    await _show_admin_panel(message)


@router.message(Command("keyinfo"))
async def keyinfo_command_handler(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("Использование: /keyinfo ABCD1234EFGH")
        return
    try:
        info = await client.admin_code_info(code)
        await message.answer(_code_card_text(info), parse_mode="HTML")
    except BackendClientError as exc:
        logger.warning("admin keyinfo failed: err=%s", exc.detail)
        await message.answer(f"Ошибка backend: {exc.detail}")


@router.message(Command("keydelete"))
async def keydelete_command_handler(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("Использование: /keydelete ABCD1234EFGH")
        return
    try:
        result = await client.admin_delete_code(code)
        action = "деактивирован" if result.get("deleted") else "уже не активен"
        await message.answer(
            "Операция выполнена:\n"
            f"Статус: {result.get('status')}\n"
            f"Результат: {action}",
        )
    except BackendClientError as exc:
        logger.warning("admin keydelete failed: err=%s", exc.detail)
        await message.answer(f"Ошибка backend: {exc.detail}")


@router.callback_query(F.data == "menu_admin")
async def admin_menu_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.message.edit_text(
        "Админ-панель",
        reply_markup=admin_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_"))
async def admin_callbacks_handler(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    data = callback.data or ""
    try:
        if data == "admin_stats":
            stats = await client.admin_stats()
            await callback.message.edit_text(
                "Статистика:\n"
                f"- Пользователи: {stats.get('users', 0)}\n"
                f"- Подписки: {stats.get('subscriptions', 0)}\n"
                f"- Активные устройства: {stats.get('active_devices', 0)}\n"
                f"- Заказы: {stats.get('orders', 0)}\n"
                f"- Оплаты: {stats.get('payments', 0)}\n"
                f"- Коды: {stats.get('codes', 0)}",
                reply_markup=admin_menu_keyboard(),
            )
        elif data == "admin_nodes":
            nodes = await client.admin_nodes()
            if not nodes:
                text = "Узлы не найдены."
            else:
                lines = ["Список узлов:"]
                for n in nodes[:20]:
                    lines.append(f"- #{n['id']} {n['name']} [{n['region_code']}] {n['status']} {n['endpoint']}")
                text = "\n".join(lines)
            await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
        elif data == "admin_grant_self":
            grant = await client.admin_grant_subscription(callback.from_user.id, 1)
            await callback.message.edit_text(
                "Бесплатная подписка продлена на 1 месяц.\n"
                f"Действует до: {format_dt(grant.get('ends_at'))}",
                reply_markup=admin_menu_keyboard(),
            )
        elif data == "admin_code_self":
            code = await client.admin_generate_code(callback.from_user.id)
            await callback.message.edit_text(
                "Код сгенерирован.\n"
                f"<code>{code.get('activation_code')}</code>\n"
                "Показывается один раз полностью.",
                parse_mode="HTML",
                reply_markup=admin_menu_keyboard(),
            )
        elif data == "admin_problem_activations":
            rows = await client.admin_problem_activations()
            if not rows:
                text = "Проблемных активаций нет."
            else:
                lines = ["Проблемные активации:"]
                for row in rows[:20]:
                    lines.append(
                        f"- {format_dt(row.get('created_at'))} | {row.get('action')} | actor={row.get('actor_id')}"
                    )
                text = "\n".join(lines)
            await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
        elif data.startswith("admin_keys_"):
            page = int(data.split("_")[2])
            await _render_keys_list(callback.message, page)
        elif data.startswith("admin_key_generate_"):
            _, _, _, code_id, page = data.split("_")
            result = await client.admin_generate_code_for_key(int(code_id))
            await callback.message.edit_text(
                "Новый код для этой подписки:\n"
                f"<code>{result.get('activation_code')}</code>",
                parse_mode="HTML",
                reply_markup=admin_key_card_keyboard(int(code_id), int(page)),
            )
        elif data.startswith("admin_key_delete_"):
            _, _, _, code_id, page = data.split("_")
            result = await client.admin_delete_code_by_id(int(code_id))
            await callback.message.edit_text(
                "Ключ обработан:\n"
                f"ID: {result.get('id')}\n"
                f"Статус: {result.get('status')}\n"
                f"Удалён: {'да' if result.get('deleted') else 'нет'}",
                reply_markup=admin_key_card_keyboard(int(code_id), int(page)),
            )
        elif data.startswith("admin_key_"):
            _, _, code_id, page = data.split("_")
            info = await client.admin_get_code(int(code_id))
            await callback.message.edit_text(
                _code_card_text(info),
                parse_mode="HTML",
                reply_markup=admin_key_card_keyboard(int(code_id), int(page)),
            )
        else:
            await callback.message.edit_text("Неизвестная админ-команда.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
    except BackendClientError as exc:
        logger.warning("admin backend call failed: action=%s err=%s", data, exc.detail)
        await callback.message.edit_text(
            f"Ошибка backend: {exc.detail}",
            reply_markup=admin_menu_keyboard(),
        )
        await callback.answer()
'@ | Set-Content $adminHandler -Encoding UTF8

@'
import logging
import re
import secrets
from uuid import uuid4

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import (
    admin_menu_keyboard,
    main_menu_keyboard,
    pay_keyboard,
    plans_keyboard,
    renew_mode_keyboard,
    renew_plans_keyboard,
)
from src.bot.utils.access import is_admin
from src.bot.utils.formatters import format_dt, parse_dt, plan_name
from src.common.config import settings

logger = logging.getLogger(__name__)

router = Router(name="user_menu")
client = BackendClient()
renew_context: dict[int, dict[str, object]] = {}
ACTIVATION_CODE_RE = re.compile(r"^[A-Z0-9]{8,32}$")


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        f"Добро пожаловать в {settings.brand_name}.\n"
        "Выберите действие в меню ниже.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.callback_query(F.data == "menu_back")
async def menu_back_handler(callback: CallbackQuery) -> None:
    renew_context.pop(callback.from_user.id, None)
    await callback.message.edit_text("Главное меню", reply_markup=main_menu_keyboard(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "menu_buy")
async def buy_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Выберите тариф для продукта «Прогрев».",
        reply_markup=plans_keyboard(),
    )
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "menu_extend_key")
async def extend_key_menu_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Как продлить подписку по ключу?",
        reply_markup=renew_mode_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renewmode_"))
async def renew_mode_handler(callback: CallbackQuery) -> None:
    mode = callback.data.split("_", 1)[1]
    await callback.message.edit_text(
        "Выберите тариф продления.",
        reply_markup=renew_plans_keyboard(mode),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renewplan_"))
async def renew_plan_handler(callback: CallbackQuery) -> None:
    _, mode, prefix, suffix = callback.data.split("_", 3)
    plan_code = f"{prefix}_{suffix}"
    renew_context[callback.from_user.id] = {
        "plan_code": plan_code,
        "issue_new_code": mode == "new",
    }
    await callback.message.edit_text(
        "Отправьте ключ активации сообщением.\n"
        "Можно вставить не только свой ключ.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"menu_help"}))
async def help_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Помощь:\n"
        "1) Выберите «Купить подписку» или «Продлить по ключу».\n"
        "2) Оплатите счет и нажмите «Я оплатил».\n"
        "3) Для продления по ключу сначала отправьте сам ключ сообщением.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "menu_vpn_config")
async def vpn_config_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        cfg = await client.get_vpn_config(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(
            f"Не удалось получить VPN-конфиг: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    import_text = (cfg.get("import_text") or "").strip()
    error = cfg.get("error")
    if error or not import_text:
        await callback.message.edit_text(
            f"VPN-конфиг пока недоступен: {error or 'empty_config'}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "Ваш VPN-конфиг:\n"
        f"<code>{import_text}</code>",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer("Конфиг готов")


@router.callback_query(F.data == "menu_my_sub")
async def my_subscription_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        status = await client.get_subscription_status(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(
            f"Не удалось получить подписку: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not status.get("active"):
        await callback.message.edit_text("Активной подписки нет.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
        return
    await callback.message.edit_text(
        "Моя подписка:\n"
        f"- Тариф: {plan_name(status.get('plan_code'))}\n"
        f"- Действует до: {format_dt(status.get('ends_at'))}\n"
        f"- Устройства: {status.get('devices_used', 0)}/{status.get('devices_limit', 5)}",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "menu_my_devices")
async def my_devices_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        devices = await client.get_user_devices(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(
            f"Не удалось получить список устройств: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not devices:
        await callback.message.edit_text("Активных устройств пока нет.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
        return
    lines = ["Мои устройства:"]
    for idx, dev in enumerate(devices[:20], start=1):
        lines.append(
            f"{idx}) {dev.get('platform', 'android')} | {dev.get('device_name', '-') or '-'}\n"
            f"   fp: {dev.get('device_fingerprint', '')[:10]}...\n"
            f"   heartbeat: {format_dt(dev.get('last_seen_at'))}"
        )
    await callback.message.edit_text("\n".join(lines), reply_markup=main_menu_keyboard(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "menu_my_codes")
async def my_codes_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    try:
        codes = await client.get_user_codes(telegram_id)
    except BackendClientError as exc:
        await callback.message.edit_text(
            f"Не удалось получить коды: {exc.detail}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not codes:
        await callback.message.edit_text("Кодов пока нет.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
        return
    lines = ["Мои коды (история):"]
    for idx, code in enumerate(codes[:20], start=1):
        lines.append(
            f"{idx}) Статус: {code.get('status')}\n"
            f"   Создан: {format_dt(code.get('created_at'))}\n"
            f"   Первое использование: {format_dt(code.get('first_redeemed_at'))}"
        )
    await callback.message.edit_text("\n".join(lines), reply_markup=main_menu_keyboard(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "menu_admin")
async def admin_shortcut_handler(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.message.edit_text("Админ-панель", reply_markup=admin_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("buy_warmup_"))
async def plan_selected_handler(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    plan_code = callback.data.replace("buy_", "", 1)
    try:
        await client.get_subscription_status(telegram_id)
        order = await client.create_order(telegram_id, plan_code)
    except BackendClientError as exc:
        logger.warning("order creation failed: tg=%s plan=%s err=%s", telegram_id, plan_code, exc.detail)
        await callback.message.edit_text(
            f"Не удалось создать заказ: {exc.detail}\nПроверьте настройки и попробуйте позже.",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    amount = order.get("amount_rub", 0)
    await callback.message.edit_text(
        "Счёт сформирован.\n"
        f"Тариф: {plan_name(plan_code)}\n"
        f"Сумма: {amount} руб\n"
        "После оплаты нажмите «Я оплатил».",
        reply_markup=pay_keyboard(order["order_id"], plan_code),
    )
    await callback.answer()


@router.message()
async def renew_key_input_handler(message: Message) -> None:
    ctx = renew_context.get(message.from_user.id)
    if not ctx:
        return
    text = (message.text or "").strip().upper()
    if not text or text.startswith("/"):
        await message.answer("Отправьте ключ обычным сообщением.")
        return
    if not ACTIVATION_CODE_RE.match(text):
        await message.answer("Некорректный формат ключа. Отправьте ключ ещё раз.")
        return

    plan_code = str(ctx.get("plan_code"))
    issue_new_code = bool(ctx.get("issue_new_code"))
    try:
        order = await client.create_order(message.from_user.id, plan_code)
    except BackendClientError as exc:
        logger.warning("renew order creation failed: tg=%s plan=%s err=%s", message.from_user.id, plan_code, exc.detail)
        await message.answer(
            f"Не удалось создать заказ: {exc.detail}",
            reply_markup=main_menu_keyboard(message.from_user.id),
        )
        renew_context.pop(message.from_user.id, None)
        return

    renew_context.pop(message.from_user.id, None)
    amount = order.get("amount_rub", 0)
    result_line = "После оплаты будет выпущен новый ключ для этой подписки." if issue_new_code else "После оплаты этот же ключ будет продлён."
    await message.answer(
        "Счёт сформирован для продления по ключу.\n"
        f"Тариф: {plan_name(plan_code)}\n"
        f"Сумма: {amount} руб\n"
        f"Ключ: <code>{text}</code>\n"
        f"{result_line}\n"
        "После оплаты нажмите «Я оплатил».",
        parse_mode="HTML",
        reply_markup=pay_keyboard(order["order_id"], plan_code, target_code=text, issue_new_code=issue_new_code),
    )


@router.callback_query(F.data.startswith("pay|") | F.data.startswith("pay_"))
async def payment_confirm_handler(callback: CallbackQuery) -> None:
    data = callback.data or ""
    target_code = None
    issue_new_code = False
    try:
        if data.startswith("pay|"):
            parts = data.split("|")
            order_id = int(parts[1])
            plan_code = parts[2]
            if len(parts) == 6 and parts[3] == "renew":
                issue_new_code = parts[4] == "new"
                target_code = parts[5].upper()
        else:
            _, order_id_str, plan_code = data.split("_", 2)
            order_id = int(order_id_str)
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные платежа.", show_alert=True)
        return

    telegram_id = callback.from_user.id
    idempotency_key = f"tg_{telegram_id}_{uuid4().hex}"
    provider_payment_id = f"sim_{secrets.token_hex(6)}"

    try:
        before = await client.get_subscription_status(telegram_id)
        result = await client.confirm_payment(
            order_id,
            provider_payment_id,
            idempotency_key,
            target_code=target_code,
            issue_new_code=issue_new_code,
        )
        after = await client.get_subscription_status(telegram_id)
    except BackendClientError as exc:
        logger.warning("payment confirm failed: tg=%s order=%s err=%s", telegram_id, order_id, exc.detail)
        await callback.message.edit_text(
            f"Не удалось подтвердить оплату: {exc.detail}\nПопробуйте позже.",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    code = result.get("activation_code", "—")
    ends_at = format_dt(after.get("ends_at"))
    before_dt = parse_dt(before.get("ends_at"))
    after_dt = parse_dt(after.get("ends_at"))
    extended = bool(before_dt and after_dt and after_dt > before_dt)
    if target_code and issue_new_code:
        extension_line = "Подписка по ключу продлена. Выпущен новый ключ."
    elif target_code:
        extension_line = "Подписка по ключу продлена. Старый ключ сохранён."
    else:
        extension_line = "Срок подписки продлён." if extended else "Подписка активирована."
    await callback.message.edit_text(
        "Оплата подтверждена.\n"
        f"{extension_line}\n\n"
        f"Ключ (показывается один раз):\n<code>{code}</code>\n\n"
        f"Тариф: {plan_name(plan_code)}\n"
        f"Действует до: {ends_at}",
        reply_markup=main_menu_keyboard(callback.from_user.id),
        parse_mode="HTML",
    )
    await callback.answer("Готово")
'@ | Set-Content $startHandler -Encoding UTF8

Write-Host 'patched admin key UI and renew-by-key flow'
