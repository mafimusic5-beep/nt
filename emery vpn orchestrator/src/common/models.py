from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.common.db import Base



def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_months: Mapped[int] = mapped_column(Integer, nullable=False)
    price_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    devices_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class VpnNode(Base):
    __tablename__ = "vpn_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="firstvds")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    config_payload: Mapped[str] = mapped_column(Text, nullable=False, default="")
    device_gate_host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    device_gate_port: Mapped[int] = mapped_column(Integer, nullable=False, default=24443)
    device_gate_server_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    device_gate_spki_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", index=True)
    load_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1000, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    capacity_clients: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    bandwidth_limit_mbps: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    current_clients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    per_device_speed_limit_mbps: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    firstvds_vps_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    provider_server_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    contract_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewal_price_eur_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    renewal_status: Mapped[str] = mapped_column(String(32), nullable=False, default="renew")
    do_not_renew_reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    ssh_private_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ssh_public_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ssh_key_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    ssh_key_status: Mapped[str] = mapped_column(String(32), nullable=False, default="missing")
    ssh_host_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    provisioning_lock_key: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    consecutive_health_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recovery_status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    recovery_lock_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_healthy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_recovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_recovery_action: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    last_recovery_error: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:
        return (
            f"<VpnNode id={self.id} name={self.name!r} endpoint={self.endpoint!r} "
            f"status={self.status!r} health={self.health_status!r} ssh_key={self.ssh_key_status!r}>"
        )


class IonosProvisionJob(Base):
    """Durable purchase journal. Never expose this table through node serializers."""

    __tablename__ = "ionos_provision_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("vpn_nodes.id"), nullable=False, unique=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    resource_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    posted_operations: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    datacenter_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    lan_id: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    server_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    # The matching public host key is pinned before the first SSH connection.
    # Protected with the same DB access controls as VpnNode.ssh_private_key.
    bootstrap_host_private_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bootstrap_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    lease_key: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    lease_token: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class ManualVpsSetupJob(Base):
    """Private, resumable installation journal; never a provider order."""

    __tablename__ = "manual_vps_setup_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("vpn_nodes.id"), nullable=False, unique=True)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    hostname: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="preflight")
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bootstrap_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    lease_token: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    devices_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("subscription_id", "device_fingerprint", name="uq_subscription_device"),
        UniqueConstraint("subscription_id", "slot_index", name="uq_subscription_device_slot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), nullable=False, index=True)
    device_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    slot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    device_name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("vpn_nodes.id"), nullable=True, index=True)


class VpnAssignment(Base):
    __tablename__ = "vpn_assignments"
    __table_args__ = (
        UniqueConstraint("subject_type", "subject_key", name="uq_vpn_assignment_subject"),
        UniqueConstraint("node_id", "client_port", name="uq_vpn_assignment_node_port"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subject_key: Mapped[str] = mapped_column(String(128), nullable=False)
    entitlement_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    entitlement_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    node_id: Mapped[int] = mapped_column(ForeignKey("vpn_nodes.id"), nullable=False, index=True)
    client_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    client_port: Mapped[int] = mapped_column(Integer, nullable=False)
    speed_limit_mbps: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="installing", index=True)
    config_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confirmation_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prepare_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_gate_enforced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False, index=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    subscription: Mapped[Subscription | None] = relationship()


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="stub")
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="RUB")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActivationCode(Base):
    __tablename__ = "activation_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    first_redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
