from datetime import datetime

from pydantic import BaseModel, Field


class PoolReservationPrepareRequest(BaseModel):
    subject_type: str = Field(default="legacy_device", pattern=r"^(legacy_device|native_device)$")
    subject_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    entitlement_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    entitlement_expires_at: datetime
    region_code: str = Field(min_length=1, max_length=16, pattern=r"^[a-z0-9-]+$")
    traffic_policy: str = Field(default="international", pattern=r"^(international|russia)$")


class PoolReservationResponse(BaseModel):
    assignment_id: int
    status: str
    confirmation_required: bool
    confirmation_token: str = ""
    node_id: int
    node_name: str
    region_code: str
    config: str
    client_port: int
    device_gate_required: bool
    device_gate_host: str
    device_gate_port: int
    device_gate_server_name: str
    device_gate_spki_sha256: str
    config_revision: int
    speed_limit_mbps: int
    entitlement_expires_at: datetime


class PoolReservationConfirmRequest(BaseModel):
    assignment_id: int = Field(gt=0)
    confirmation_token: str = Field(min_length=32, max_length=128)


class PoolReservationConfirmResponse(BaseModel):
    assignment_id: int
    status: str
    confirmed_at: datetime


class PoolAssignmentMaintenanceResponse(BaseModel):
    checked: int
    migrated: int
    revoked: int
    failed: int
