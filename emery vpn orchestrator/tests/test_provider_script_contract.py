from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from src.backend.services.node_adapters import ShellScriptNodeProvisioningService
from src.common.config import settings
from src.common.models import VpnNode


CONFIG = (
    "vless://11111111-1111-4111-8111-111111111111@203.0.113.10:443"
    "?type=tcp&security=reality&pbk=public-key&sid=0123456789abcdef#Germany"
)


def node() -> VpnNode:
    return VpnNode(
        id=7,
        region_code="de",
        name="auto-node",
        provider="ionos_vps_plus",
        status="draft",
        endpoint="",
        config_payload="",
        health_status="unknown",
        capacity_clients=20,
        current_clients=0,
        bandwidth_limit_mbps=600,
        per_device_speed_limit_mbps=30,
    )


def test_provider_response_must_attest_all_runtime_controls(monkeypatch):
    monkeypatch.setattr(settings, "node_provision_script", "/provider/order")
    monkeypatch.setattr(settings, "auto_provision_provider", "ionos_vps_plus")
    response = {
        "ok": True,
        "provider_server_id": "srv-1",
        "contract_id": "contract-1",
        "endpoint": "203.0.113.10",
        "config_payload": CONFIG,
        "bootstrap_verified": True,
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(response), ""),
    )

    result = ShellScriptNodeProvisioningService().provision_node(node())

    assert result["status"] == "failed"
    assert result["detail"] == "provider_safety_attestation_missing"
    assert "dedicated_port_range_open" in result["missing"]


def test_verified_ionos_adapter_activates_contract_metadata(monkeypatch):
    monkeypatch.setattr(settings, "node_provision_script", "/provider/order")
    monkeypatch.setattr(settings, "auto_provision_provider", "ionos_vps_plus")
    paid_until = datetime(2026, 9, 10, tzinfo=timezone.utc)
    response = {
        "ok": True,
        "provider_server_id": "srv-1",
        "contract_id": "contract-1",
        "endpoint": "203.0.113.10",
        "config_payload": CONFIG,
        "paid_until": paid_until.isoformat(),
        "renewal_price_eur_cents": 500,
        "auto_renew": True,
        "bootstrap_verified": True,
        "credential_transport_ready": True,
        "dedicated_port_range_open": True,
        "rate_limit_ready": True,
        "smtp_block_enforced": True,
        "shared_credential_disabled": True,
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(response), ""),
    )
    target = node()

    result = ShellScriptNodeProvisioningService().provision_node(target)

    assert result["status"] == "ok"
    assert target.provider_server_id == "srv-1"
    assert target.contract_id == "contract-1"
    assert target.endpoint == "203.0.113.10"
    assert target.paid_until == paid_until
    assert target.renewal_price_eur_cents == 500


def test_unconfigured_secondary_healthcheck_preserves_recovery_status(monkeypatch):
    monkeypatch.setattr(settings, "node_healthcheck_script", "")
    target = node()
    target.status = "active"
    target.health_status = "down"

    result = ShellScriptNodeProvisioningService().healthcheck_nodes([target])

    assert result == [
        {
            "node_id": target.id,
            "health_status": "down",
            "load_score": target.load_score,
        }
    ]
