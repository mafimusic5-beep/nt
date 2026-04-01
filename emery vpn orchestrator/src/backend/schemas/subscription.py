from datetime import datetime

from pydantic import BaseModel, Field


class RedeemActivationCodeRequest(BaseModel):
    code: str = Field(min_length=12, max_length=64)
    telegram_id: int
    device_fingerprint: str = Field(min_length=6, max_length=128)
    platform: str = "android"
    device_name: str = ""


class RedeemActivationCodeResponse(BaseModel):
    valid: bool
    expires_at: datetime | None = None
    plan_name: str | None = None
    subscription_id: int | None = None


class SubscriptionStatusResponse(BaseModel):
    active: bool
    subscription_id: int | None = None
    plan_code: str | None = None
    ends_at: datetime | None = None
    devices_used: int = 0
    devices_limit: int = 5


class RegisterDeviceRequest(BaseModel):
    telegram_id: int
    device_fingerprint: str = Field(min_length=6, max_length=128)
    platform: str = "android"
    device_name: str = ""


class RegisterDeviceResponse(BaseModel):
    device_id: int
    devices_used: int
    devices_limit: int


class HeartbeatRequest(BaseModel):
    telegram_id: int
    device_fingerprint: str


class UnbindDeviceRequest(BaseModel):
    telegram_id: int
    device_fingerprint: str


class VpnConfigResponse(BaseModel):
    import_text: str | None = None
    error: str | None = None


class VpnServerItemResponse(BaseModel):
    id: int
    city: str
    health_status: str
    is_available: bool


class VpnConnectRequest(BaseModel):
    access_key: str = Field(min_length=1, max_length=128)
    server_id: int


class VpnConnectResponse(BaseModel):
    server_id: int
    city: str
    import_text: str


class UserDeviceResponse(BaseModel):
    device_fingerprint: str
    platform: str
    device_name: str
    last_seen_at: datetime | None = None


class UserCodeResponse(BaseModel):
    status: str
    created_at: datetime
    first_redeemed_at: datetime | None = None
