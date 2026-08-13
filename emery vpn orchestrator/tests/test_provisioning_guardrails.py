from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.backend.services.provisioning_guard_service import ProvisioningGuardService
from src.common.config import settings


def _node(
    node_id: int,
    *,
    region: str = "de",
    status: str = "active",
    created_at: datetime | None = None,
):
    return SimpleNamespace(
        id=node_id,
        region_code=region,
        status=status,
        created_at=created_at or datetime.now(timezone.utc) - timedelta(days=10),
    )


def _enable_safe_script_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auto_provision_enabled", True)
    monkeypatch.setattr(settings, "auto_provision_provider", "script")
    monkeypatch.setattr(settings, "node_provision_script", "/opt/emery/bin/provider-order")
    monkeypatch.setattr(settings, "auto_provision_server_monthly_cost_eur", 18)
    monkeypatch.setattr(settings, "auto_provision_monthly_budget_eur", 100)
    monkeypatch.setattr(settings, "auto_provision_max_servers_per_hour", 1)
    monkeypatch.setattr(settings, "auto_provision_max_servers_per_day", 2)
    monkeypatch.setattr(settings, "pool_accounting_bridge_enabled", True)
    monkeypatch.setattr(settings, "pool_bridge_api_key", "test-pool-bridge-key")
    monkeypatch.setattr(settings, "unique_device_credentials_enabled", True)
    monkeypatch.setattr(settings, "per_device_rate_limit_enforced", True)
    monkeypatch.setattr(settings, "smtp_abuse_protection_enabled", True)


def test_guard_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "auto_provision_enabled", False)

    result = ProvisioningGuardService().evaluate(region_code="de", nodes=[])

    assert result.allowed is False
    assert result.reason == "auto_provision_disabled"


def test_ionos_vps_plus_fails_closed_without_ordering_adapter(monkeypatch):
    _enable_safe_script_mode(monkeypatch)
    monkeypatch.setattr(settings, "auto_provision_provider", "ionos_vps_plus")
    monkeypatch.setattr(settings, "node_provision_script", "")

    result = ProvisioningGuardService().evaluate(region_code="de", nodes=[])

    assert result.allowed is False
    assert result.reason == "provider_ordering_adapter_not_configured"


def test_ionos_vps_plus_can_use_configured_strict_adapter(monkeypatch):
    _enable_safe_script_mode(monkeypatch)
    monkeypatch.setattr(settings, "auto_provision_provider", "ionos_vps_plus")

    result = ProvisioningGuardService().evaluate(region_code="de", nodes=[])

    assert result.allowed is True


def test_pending_region_request_prevents_duplicate_purchase(monkeypatch):
    _enable_safe_script_mode(monkeypatch)

    result = ProvisioningGuardService().evaluate(
        region_code="de",
        nodes=[_node(1, status="provisioning")],
    )

    assert result.allowed is False
    assert result.reason == "region_provisioning_already_in_progress"


def test_missing_unique_device_credentials_blocks_paid_scale_out(monkeypatch):
    _enable_safe_script_mode(monkeypatch)
    monkeypatch.setattr(settings, "unique_device_credentials_enabled", False)

    result = ProvisioningGuardService().evaluate(region_code="de", nodes=[])

    assert result.allowed is False
    assert result.reason == "unique_device_credentials_not_enforced"


def test_missing_registered_device_accounting_blocks_paid_scale_out(monkeypatch):
    _enable_safe_script_mode(monkeypatch)
    monkeypatch.setattr(settings, "pool_accounting_bridge_enabled", False)

    result = ProvisioningGuardService().evaluate(region_code="de", nodes=[])

    assert result.allowed is False
    assert result.reason == "registered_device_capacity_accounting_not_connected"


def test_monthly_budget_counts_recurring_servers(monkeypatch):
    _enable_safe_script_mode(monkeypatch)
    monkeypatch.setattr(settings, "auto_provision_monthly_budget_eur", 50)

    result = ProvisioningGuardService().evaluate(
        region_code="de",
        nodes=[_node(1), _node(2, region="nl")],
    )

    assert result.allowed is False
    assert result.reason == "monthly_budget_exceeded"
    assert result.projected_monthly_cost_eur == 54


def test_guard_allows_one_budgeted_request(monkeypatch):
    _enable_safe_script_mode(monkeypatch)

    result = ProvisioningGuardService().evaluate(
        region_code="de",
        nodes=[_node(1, region="nl")],
    )

    assert result.allowed is True
    assert result.projected_monthly_cost_eur == 36
