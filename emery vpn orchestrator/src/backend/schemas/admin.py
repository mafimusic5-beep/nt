from datetime import datetime

from pydantic import BaseModel, Field, SecretStr


class GrantSubscriptionRequest(BaseModel):
    telegram_id: int
    months: int
    region_code: str = "moscow"


class GrantSubscriptionResponse(BaseModel):
    subscription_id: int
    ends_at: datetime


class ManualNodeBootstrapRequest(BaseModel):
    name: str = "Server"
    region_code: str
    endpoint: str
    ssh_user: str = "root"
    ssh_password: SecretStr
    capacity_clients: int = Field(default=5, ge=1, le=100)
    bandwidth_limit_mbps: int = Field(default=1000, ge=1, le=100000)
    per_device_speed_limit_mbps: int = Field(default=100, ge=1, le=10000)
    device_gate_host: str = ""
    device_gate_port: int = 24443
    device_gate_server_name: str = ""
    device_gate_spki_sha256: str = ""


class VpnNodeUpsertRequest(BaseModel):
    name: str
    region_code: str = "moscow"
    endpoint: str
    config_payload: str
    device_gate_host: str = ""
    device_gate_port: int = 24443
    device_gate_server_name: str = ""
    device_gate_spki_sha256: str = ""
    provider: str = "manual"
    status: str = "active"
    health_status: str = "unknown"
    load_score: int = 1000
    priority: int = 0
    capacity_clients: int = 20
    bandwidth_limit_mbps: int = 600
    current_clients: int = 0
    per_device_speed_limit_mbps: int = 30
    firstvds_vps_id: str = ""
    provider_server_id: str = ""
    contract_id: str = ""
    paid_until: datetime | None = None
    renewal_price_eur_cents: int = 0
    auto_renew: bool = True
    renewal_status: str = "renew"
    do_not_renew_reason: str = ""
    ssh_key_fingerprint: str = ""
    ssh_key_status: str = "missing"
    ssh_host_key: str = ""


class VpnNodeDeviceGateRequest(BaseModel):
    device_gate_host: str
    device_gate_port: int = 24443
    device_gate_server_name: str
    device_gate_spki_sha256: str


class VpnNodeResponse(BaseModel):
    id: int
    name: str
    region_code: str
    provider: str = "manual"
    endpoint: str
    device_gate_host: str = ""
    device_gate_port: int = 24443
    device_gate_server_name: str = ""
    device_gate_spki_sha256: str = ""
    status: str
    health_status: str
    load_score: int
    priority: int
    capacity_clients: int
    current_clients: int
    bandwidth_limit_mbps: int
    per_device_speed_limit_mbps: int
    provider_server_id: str = ""
    contract_id: str = ""
    paid_until: datetime | None = None
    renewal_price_eur_cents: int = 0
    auto_renew: bool = True
    renewal_status: str = "renew"
    do_not_renew_reason: str = ""
    ssh_key_fingerprint: str
    ssh_key_status: str
    ssh_host_key_pinned: bool = False
    has_valid_config: bool = False
    consecutive_health_failures: int = 0
    recovery_status: str = "idle"
    recovery_lock_until: datetime | None = None
    last_healthy_at: datetime | None = None
    last_recovery_at: datetime | None = None
    last_recovery_action: str = ""
    last_recovery_error: str = ""


class ManualNodeBootstrapResponse(BaseModel):
    node: VpnNodeResponse
    policy_ready: bool = True
    isp_egress_enabled: bool = False


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
