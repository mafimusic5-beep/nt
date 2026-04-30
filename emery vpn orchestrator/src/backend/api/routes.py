from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.backend.deps.auth import require_admin_api_key, require_internal_api_key
from src.backend.deps.db import get_db
from src.backend.schemas.admin import (
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
from src.backend.utils.debug_log import agent_log
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
    VpnConnectRequest,
    VpnConnectResponse,
    VpnConfigResponse,
    VpnServerItemResponse,
)
from src.backend.services.admin_service import AdminService
from src.backend.services.capacity_service import CapacityService
from src.backend.services.order_service import OrderService
from src.backend.services.subscription_service import SubscriptionService

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
    # #region agent log
    agent_log(
        hypothesis_id="H2",
        location="routes.py:get_vpn_config",
        message="vpn_config_requested",
        data={"telegram_id": telegram_id},
    )
    # #endregion
    return SubscriptionService(db).get_vpn_config(telegram_id)


@router.get("/vpn/servers", response_model=list[VpnServerItemResponse])
def get_vpn_servers(db: Session = Depends(get_db)):
    return SubscriptionService(db).list_vpn_servers()


@router.get("/vpn/pool/config")
def get_vpn_pool_config(access_key: str, db: Session = Depends(get_db)):
    return SubscriptionService(db).get_vpn_pool_config(access_key)


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
    # #region agent log
    agent_log(
        hypothesis_id="H1",
        location="routes.py:internal_confirm_payment",
        message="internal_confirm_payment_called",
        data={
            "order_id": payload.order_id,
            "paid": payload.paid,
            "provider_payment_id_prefix": payload.provider_payment_id[:8],
        },
    )
    # #endregion
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


@router.get("/admin/capacity", dependencies=[Depends(require_admin_api_key)])
def admin_capacity(db: Session = Depends(get_db)):
    return {"regions": [row.__dict__ for row in CapacityService(db).list_regions()]}


@router.get("/admin/capacity/alert", dependencies=[Depends(require_admin_api_key)])
def admin_capacity_alert(db: Session = Depends(get_db)):
    return {"text": CapacityService(db).alert_text()}


@router.get("/admin/stats", response_model=AdminStatsResponse, dependencies=[Depends(require_admin_api_key)])
def admin_stats(db: Session = Depends(get_db)):
    return AdminService(db).stats()


@router.post("/admin/codes/generate", response_model=ManualCodeResponse, dependencies=[Depends(require_admin_api_key)])
def admin_generate_code(telegram_id: int, db: Session = Depends(get_db)):
    return AdminService(db).generate_code(telegram_id)


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
