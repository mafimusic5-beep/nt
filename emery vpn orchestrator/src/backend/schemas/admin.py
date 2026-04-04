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


class ActivationCodeInfoResponse(BaseModel):
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
    code_hash: str
    status: str
    deleted: bool
