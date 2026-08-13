from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.common.config import settings


@dataclass(frozen=True, slots=True)
class ProvisioningGuardDecision:
    allowed: bool
    reason: str
    projected_monthly_cost_eur: int = 0


class ProvisioningGuardService:
    """Fail-closed financial and idempotency checks for automatic purchases."""

    SUPPORTED_AUTOMATIC_PROVIDERS = {"firstvds", "script", "ionos_vps_plus"}

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def evaluate(self, *, region_code: str, nodes: list, now: datetime | None = None) -> ProvisioningGuardDecision:
        if not settings.auto_provision_enabled:
            return ProvisioningGuardDecision(False, "auto_provision_disabled")

        provider = settings.auto_provision_provider.strip().lower()
        if provider not in self.SUPPORTED_AUTOMATIC_PROVIDERS:
            return ProvisioningGuardDecision(False, "auto_provision_provider_unconfigured")
        if provider in {"script", "ionos_vps_plus"} and not settings.node_provision_script.strip():
            return ProvisioningGuardDecision(False, "provider_ordering_adapter_not_configured")
        if not settings.pool_accounting_bridge_enabled:
            return ProvisioningGuardDecision(
                False,
                "registered_device_capacity_accounting_not_connected",
            )
        if not settings.pool_bridge_api_key.strip():
            return ProvisioningGuardDecision(False, "pool_bridge_key_not_configured")
        if not settings.unique_device_credentials_enabled:
            return ProvisioningGuardDecision(False, "unique_device_credentials_not_enforced")
        if not settings.per_device_rate_limit_enforced:
            return ProvisioningGuardDecision(False, "per_device_rate_limit_not_enforced")
        if not settings.smtp_abuse_protection_enabled:
            return ProvisioningGuardDecision(False, "smtp_abuse_protection_not_enforced")
        available_ports = max(
            int(settings.xray_client_port_end) - int(settings.xray_client_port_start) + 1,
            0,
        )
        if available_ports < max(int(settings.pool_node_capacity_devices), 1):
            return ProvisioningGuardDecision(False, "dedicated_port_range_too_small")

        monthly_cost = max(int(settings.auto_provision_server_monthly_cost_eur), 0)
        budget = max(int(settings.auto_provision_monthly_budget_eur), 0)
        if monthly_cost <= 0 or budget <= 0:
            return ProvisioningGuardDecision(False, "auto_provision_budget_unconfigured")

        if any(
            node.region_code == region_code and node.status in {"draft", "provisioning", "provision_failed"}
            for node in nodes
        ):
            return ProvisioningGuardDecision(False, "region_provisioning_already_in_progress")

        current_time = now or datetime.now(timezone.utc)
        hour_cutoff = current_time - timedelta(hours=1)
        day_cutoff = current_time - timedelta(days=1)
        created_last_hour = sum(self._utc(node.created_at) >= hour_cutoff for node in nodes)
        created_last_day = sum(self._utc(node.created_at) >= day_cutoff for node in nodes)
        if created_last_hour >= max(int(settings.auto_provision_max_servers_per_hour), 0):
            return ProvisioningGuardDecision(False, "hourly_server_limit_reached")
        if created_last_day >= max(int(settings.auto_provision_max_servers_per_day), 0):
            return ProvisioningGuardDecision(False, "daily_server_limit_reached")

        # Servers are never automatically deleted. Every known node is therefore
        # treated as a recurring bill until an operator records otherwise.
        projected = (len(nodes) + 1) * monthly_cost
        if projected > budget:
            return ProvisioningGuardDecision(False, "monthly_budget_exceeded", projected)
        return ProvisioningGuardDecision(True, "allowed", projected)
