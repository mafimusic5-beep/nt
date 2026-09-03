from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.backend.services.ionos_cloud_api import IonosCloudApi, IonosApiError
from src.backend.services.manual_vps_bootstrap import ManualVpsBootstrap
from src.backend.services.manual_vps_config import (
    ManualVpsError, bootstrap_profile, node_spec, private_file, setup_guard,
)
from src.backend.services.manual_vps_setup import ManualVpsSetupService
from src.backend.services.node_orchestration_service import NodeOrchestrationService
from src.backend.services.node_recovery_service import SshAndProviderRecoveryTransport, VlessTcpProbe
from src.backend.services.provisioning_guard_service import ProvisioningGuardService
from src.backend.services.renewal_planner_service import RenewalPlannerService
from src.backend.services.xray_credential_service import VlessDeviceConfigBuilder
from src.common.config import settings
from src.common.models import AuditLog, ManualVpsSetupJob, VpnAssignment, VpnNode

IP = "93.184.216.34"  # Fixture only; no tests contact this address.
KEY = "manual-fixture-gate-key-" + "a" * 40


def write_private(path, value):
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)
    path.chmod(0o600)
    return str(path)


@pytest.fixture
def configured(monkeypatch, tmp_path):
    values = {
        "manual_vps_setup_enabled": True, "auto_provision_enabled": False,
        "ionos_cloud_apply_enabled": False, "auto_provision_provider": "unconfigured",
        "pool_accounting_bridge_enabled": True, "pool_bridge_api_key": "fixture-pool",
        "unique_device_credentials_enabled": True, "per_device_rate_limit_enforced": True,
        "smtp_abuse_protection_enabled": True, "device_bound_gate_enabled": True,
        "manual_vps_gate_authorize_key": SecretStr(KEY), "recovery_ssh_user": "root",
        "recovery_allow_unknown_host_keys": False, "xray_config_path": "/usr/local/etc/xray/config.json",
        "xray_credential_script": "", "device_gate_service_name": "emery-device-gate",
        "regional_policy_sync_script": "/opt/emery/regional-policy/regional_policy.py",
        "xray_client_port_start": 20000, "xray_client_port_end": 20199,
        "pool_per_device_speed_limit_mbps": 30, "ionos_cloud_token": SecretStr(""),
        "ionos_cloud_dns_token": SecretStr(""), "ionos_cloud_contract_number": "",
        "manual_vps_profile_path": write_private(tmp_path / "profile.json", {
            "management_ipv4": "9.9.9.9", "authorize_url": "https://control.example.com/internal/device-gate/authorize",
            "acme_email": "operator@example.com", "acme_terms_accepted": True,
            "xray_version": "26.8.1", "xray_sha256": "a" * 64,
            "reality_server_name": "example.com", "probe_url": "https://example.com/",
            "bootstrap_timeout_seconds": 7200,
        }),
    }
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)
    # A manually bought node must work WITHOUT even constructing a Cloud API.
    monkeypatch.setattr(IonosCloudApi, "__init__", lambda *a, **kw: pytest.fail("provider API was constructed"))
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption(),
    ).decode()
    host = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
    ).decode()
    path = write_private(tmp_path / "node.json", {
        "name": "Frankfurt manual VPS", "region_code": "de", "endpoint": IP,
        "hostname": "frankfurt.vpn.example.com", "capacity_clients": 20, "bandwidth_limit_mbps": 600,
        "ssh_private_key_path": write_private(tmp_path / "id_ed25519", private), "ssh_host_key": host,
    })
    return {"spec": node_spec(path), "node_file": path, "private": private, "profile_file": values["manual_vps_profile_path"]}


class FakeBootstrap:
    def __init__(self):
        self.preflights = 0
        self.starts = 0
        self.verifications = 0
        self.ready = True
        self.fail_preflight = False
        self.fail_verify = False
        self.override = {}

    def preflight(self, node, job, profile, **kwargs):
        self.preflights += 1
        if self.fail_preflight:
            raise ManualVpsError("manual_vps_existing_software_requires_review")

    def start(self, node, job, profile):
        self.starts += 1

    def inspect(self, node, job):
        if not self.ready:
            return None
        profile = json.loads(job.config_json)
        return {
            "operation_id": job.id, "endpoint": node.endpoint, "hostname": profile["hostname"],
            "bootstrap_verified": True, "regional_policy_ready": True, "control_api_verified": True,
            "certificate_verified": True, "spki_sha256": "b" * 64,
            "config_payload": f"vless://11111111-1111-4111-8111-111111111111@{node.endpoint}:443?type=tcp&security=reality&pbk={'a' * 43}&sid=0123456789abcdef&flow=xtls-rprx-vision",
            **self.override,
        }

    def verify_data_plane(self, node, job, profile):
        self.verifications += 1
        if self.fail_verify:
            raise IonosApiError("ionos_vpn_canary_failed")


def register(db, configured, fake=None):
    fake = fake or FakeBootstrap()
    service = ManualVpsSetupService(db, bootstrap=fake)
    result = service.register(configured["spec"])
    return service, fake, db.get(VpnNode, result["node_id"])


def reach(service, db, node, phase):
    for _ in range(10):
        job = db.scalar(select(ManualVpsSetupJob).where(ManualVpsSetupJob.node_id == node.id))
        if job.phase == phase:
            return job
        assert service.advance(node.id)["status"] == "pending"
    pytest.fail("setup did not reach the requested phase")


def test_setup_without_any_provider_credentials_only_publishes_after_verification(configured, db_session):
    service, fake, node = register(db_session, configured)
    job = reach(service, db_session, node, "verify")
    assert node.status == "provisioning"
    assert NodeOrchestrationService(db_session).list_connectable_nodes() == []
    assert fake.starts == 1 and fake.verifications == 0
    assert service.advance(node.id)["status"] == "ok"
    assert node.status == "active" and node.health_status == "healthy"
    assert node.renewal_status == "owner_managed"
    assert not node.contract_id and not node.provider_server_id and node.paid_until is None
    assert NodeOrchestrationService(db_session).list_connectable_nodes() == [node]
    assert fake.verifications == 1 and job.phase == "ready" and job.lease_until is None
    assert service.advance(node.id)["status"] == "ok" and fake.starts == 1
    assignment = VpnAssignment(id=9, node_id=node.id, client_uuid="11111111-1111-4111-8111-111111111111", client_port=20000)
    uri = VlessDeviceConfigBuilder.build(node, assignment)
    assert "eg_spki=" in uri and "flow=xtls-rprx-vision" in uri and "@127.0.0.1:" in uri
    outputs = json.dumps(service.status()) + job.config_json + " ".join(db_session.scalars(select(AuditLog.details))) + uri
    assert configured["private"].strip() not in outputs and KEY not in outputs
    assert configured["private"].strip() not in repr(configured["spec"])


def test_check_is_read_only(configured, db_session):
    fake = FakeBootstrap()
    result = ManualVpsSetupService(db_session, bootstrap=fake).check(configured["spec"])
    assert result["no_changes_made"] and fake.preflights == 1 and fake.starts == 0
    assert db_session.scalar(select(ManualVpsSetupJob)) is None
    assert db_session.scalar(select(VpnNode)) is None


@pytest.mark.parametrize("flag,value", [
    ("manual_vps_setup_enabled", False), ("auto_provision_enabled", True),
    ("ionos_cloud_apply_enabled", True), ("device_bound_gate_enabled", False),
    ("unique_device_credentials_enabled", False), ("per_device_rate_limit_enforced", False),
    ("smtp_abuse_protection_enabled", False), ("pool_accounting_bridge_enabled", False),
    ("pool_bridge_api_key", ""), ("recovery_allow_unknown_host_keys", True),
    ("recovery_ssh_user", "nobody"), ("xray_credential_script", "/tmp/unknown-installer"),
])
def test_missing_safeguard_stops_before_any_ssh(configured, db_session, monkeypatch, flag, value):
    monkeypatch.setattr(settings, flag, value)
    fake = FakeBootstrap()
    with pytest.raises(ManualVpsError):
        ManualVpsSetupService(db_session, bootstrap=fake).register(configured["spec"])
    assert fake.preflights == 0 and fake.starts == 0
    assert db_session.scalar(select(VpnNode)) is None


def test_automatic_ordering_is_disabled_even_if_misconfigured(configured, monkeypatch):
    monkeypatch.setattr(settings, "auto_provision_enabled", True)
    monkeypatch.setattr(settings, "ionos_cloud_apply_enabled", True)
    monkeypatch.setattr(settings, "auto_provision_provider", "ionos_cloud")
    result = ProvisioningGuardService().evaluate(region_code="de", nodes=[])
    assert not result.allowed and result.reason == "manual_vps_setup_disallows_automatic_purchases"


def test_existing_server_is_untouched(configured, db_session):
    existing = VpnNode(name="Existing VPN", region_code="de", endpoint=IP, provider="firstvds",
                       status="active", health_status="healthy", config_payload="untouched")
    db_session.add(existing)
    db_session.commit()
    fake = FakeBootstrap()
    with pytest.raises(ManualVpsError, match="already_registered"):
        ManualVpsSetupService(db_session, bootstrap=fake).register(configured["spec"])
    assert fake.preflights == 0 and existing.config_payload == "untouched" and existing.status == "active"
    assert db_session.scalar(select(ManualVpsSetupJob)) is None


def test_management_host_is_rejected(configured, db_session):
    with pytest.raises(ManualVpsError, match="management_server"):
        ManualVpsSetupService(db_session, bootstrap=FakeBootstrap()).register(
            replace(configured["spec"], endpoint="9.9.9.9"),
        )


def test_failed_remote_preflight_rolls_back_registration(configured, db_session):
    fake = FakeBootstrap()
    fake.fail_preflight = True
    with pytest.raises(ManualVpsError, match="existing_software"):
        ManualVpsSetupService(db_session, bootstrap=fake).register(configured["spec"])
    assert db_session.scalar(select(VpnNode)) is None
    assert db_session.scalar(select(ManualVpsSetupJob)) is None


def test_duplicate_registration_returns_same_job_and_never_reinstalls(configured, db_session):
    service, fake, node = register(db_session, configured)
    assert service.register(configured["spec"])["node_id"] == node.id
    assert len(list(db_session.scalars(select(VpnNode)))) == 1 and fake.preflights == 1
    with pytest.raises(ManualVpsError, match="configuration_mismatch"):
        service.register(replace(configured["spec"], name="Other name"))


@pytest.mark.parametrize("field,value", [
    ("bootstrap_verified", False), ("regional_policy_ready", False),
    ("control_api_verified", False), ("certificate_verified", False),
    ("endpoint", "8.8.8.8"), ("hostname", "other.example.com"),
    ("operation_id", "other-job"), ("spki_sha256", "not-a-pin"),
    ("config_payload", "arbitrary"),
])
def test_bad_readiness_is_paused_and_never_published(configured, db_session, field, value):
    service, fake, node = register(db_session, configured)
    reach(service, db_session, node, "bootstrapping")
    fake.override[field] = value
    assert service.advance(node.id)["status"] == "blocked"
    assert node.status == "provisioning"
    assert service.tick()["status"] == "idle"
    assert NodeOrchestrationService(db_session).list_connectable_nodes() == []


def test_data_plane_failure_requires_explicit_retry(configured, db_session):
    service, fake, node = register(db_session, configured)
    reach(service, db_session, node, "verify")
    fake.fail_verify = True
    assert service.advance(node.id)["status"] == "blocked"
    fake.fail_verify = False
    assert service.advance(node.id)["status"] == "blocked" and fake.verifications == 1
    assert service.advance(node.id, retry=True)["status"] == "ok"
    assert fake.verifications == 2 and fake.starts == 1


def test_resume_uses_same_job_keys_and_does_not_restart_running_installer(configured, db_session):
    service, fake, node = register(db_session, configured)
    job = reach(service, db_session, node, "bootstrapping")
    fake.ready = False
    assert service.advance(node.id)["status"] == "pending"
    saved_key, saved_id = node.ssh_private_key, job.id
    service = ManualVpsSetupService(db_session, bootstrap=fake)
    assert service.advance(node.id)["status"] == "pending"
    fake.ready = True
    reach(service, db_session, node, "verify")
    assert service.advance(node.id)["status"] == "ok"
    assert job.id == saved_id and node.ssh_private_key == saved_key and fake.starts == 1


@pytest.mark.parametrize("field,value", [
    ("endpoint", "8.8.8.8"), ("ssh_host_key", "changed"), ("ssh_private_key", "changed"),
    ("current_clients", 1), ("status", "active"), ("name", "changed"),
])
def test_identity_changes_or_existing_clients_stop_installation(configured, db_session, field, value):
    service, fake, node = register(db_session, configured)
    setattr(node, field, value)
    db_session.commit()
    assert service.advance(node.id)["status"] == "blocked" and fake.starts == 0


def test_profile_or_secret_change_blocks_resume(configured, db_session, monkeypatch):
    service, fake, node = register(db_session, configured)
    monkeypatch.setattr(settings, "manual_vps_gate_authorize_key", SecretStr("b" * 40))
    assert service.advance(node.id)["detail"] == "manual_vps_job_configuration_changed"
    assert fake.starts == 0


def test_expired_job_stops_for_review(configured, db_session):
    service, fake, node = register(db_session, configured)
    job = db_session.scalar(select(ManualVpsSetupJob))
    job.deadline_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()
    assert service.advance(node.id)["detail"] == "manual_vps_setup_deadline_requires_review"
    assert fake.starts == 0
    assert service.advance(node.id, retry=True)["status"] == "pending"
    assert job.phase == "bootstrap"


def test_two_controllers_cannot_run_the_same_step(configured, db_session):
    first, fake, node = register(db_session, configured)
    job = db_session.scalar(select(ManualVpsSetupJob))
    assert first._claim(job)
    try:
        with Session(db_session.get_bind(), autoflush=False) as second_db:
            second = ManualVpsSetupService(second_db, bootstrap=fake)
            assert second.advance(node.id)["status"] == "pending"
            assert fake.preflights == 1 and fake.starts == 0
    finally:
        first._release(job.id)


def test_lost_lease_does_not_publish_or_flush_stale_node(configured, db_session):
    service, _, node = register(db_session, configured)
    job = db_session.scalar(select(ManualVpsSetupJob))
    assert service._claim(job)
    node.status = "active"  # Uncommitted, stale worker mutation.
    with Session(db_session.get_bind()) as other:
        current = other.get(ManualVpsSetupJob, job.id)
        current.lease_token = "new-worker"
        other.commit()
    with pytest.raises(ManualVpsError, match="lease_lost"):
        service._save(job)
    service._release(job.id)
    db_session.expire_all()
    assert node.status == "provisioning" and job.lease_token == "new-worker"


def test_arbitrary_admin_created_node_cannot_be_bootstrapped(configured, db_session):
    node = VpnNode(name="not registered by CLI", region_code="de", endpoint=IP, provider="manual_vps")
    db_session.add(node)
    db_session.commit()
    result = NodeOrchestrationService(db_session).provision_node(node.id)
    assert result["status"] == "blocked" and result["detail"] == "manual_vps_registered_job_required"


def test_normal_user_cannot_invoke_existing_admin_setup_route(configured, db_session, monkeypatch):
    from src.backend.api.routes import router
    from src.backend.deps.db import get_db
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr(settings, "admin_api_key", "fixture-admin-only")
    with TestClient(app) as client:
        response = client.post("/api/v1/admin/nodes/1/provision", headers={"X-Admin-Api-Key": "wrong"})
    assert response.status_code == 403
    assert db_session.scalar(select(ManualVpsSetupJob)) is None


def test_recovery_uses_public_gateway_and_no_provider_fallback(configured, monkeypatch):
    node = VpnNode(provider="manual_vps", endpoint=IP, device_gate_port=24443)
    assert VlessTcpProbe.endpoint(node) == (IP, 24443)
    transport = SshAndProviderRecoveryTransport()
    monkeypatch.setattr(transport, "_connect", MagicMock(side_effect=OSError("offline")))
    monkeypatch.setattr(settings, "recovery_provider_reboot_script", "/must/not/run")
    result = transport.reboot_server(node)
    assert not result.ok and result.detail == "manual_vps_ssh_reboot_failed_provider_actions_disabled"


def test_renewal_planner_cannot_cancel_manually_purchased_nodes(configured, db_session, monkeypatch):
    for index in range(3):
        db_session.add(VpnNode(name=str(index), region_code="de", provider="manual_vps", capacity_clients=20,
                              current_clients=0, auto_renew=True, contract_id=str(index),
                              paid_until=datetime.now(timezone.utc), status="active"))
    db_session.commit()
    monkeypatch.setattr(settings, "auto_renewal_actions_enabled", True)
    transport = MagicMock()
    result = RenewalPlannerService(db_session, transport=transport).apply()
    assert result["actions"] == []
    transport.disable_auto_renew.assert_not_called()


def test_dns_proxy_stale_aaaa_and_control_host_are_rejected(configured, monkeypatch):
    from src.backend.services import manual_vps_bootstrap as module
    node = VpnNode(endpoint=IP)
    profile = dict(bootstrap_profile(), hostname=configured["spec"].hostname)
    monkeypatch.setattr(module, "resolve_host", lambda name: {IP, "::1"})
    with pytest.raises(ManualVpsError, match="dns_must_point"):
        ManualVpsBootstrap.check_dns(node, profile)
    monkeypatch.setattr(module, "resolve_host", lambda name: {IP})
    with pytest.raises(ManualVpsError, match="authorization_server"):
        ManualVpsBootstrap.check_dns(node, profile)
    monkeypatch.setattr(module, "resolve_host", lambda name: {IP} if name == profile["hostname"] else {"9.9.9.9"})
    ManualVpsBootstrap.check_dns(node, profile)


def test_missing_host_key_or_injection_is_rejected(configured):
    from pathlib import Path
    path = Path(configured["node_file"])
    value = json.loads(path.read_text())
    for field, invalid in (("ssh_host_key", ""), ("endpoint", "127.0.0.1"),
                           ("hostname", "example.com;whoami"), ("capacity_clients", True)):
        altered = dict(value, **{field: invalid})
        write_private(path, altered)
        with pytest.raises(ManualVpsError):
            node_spec(str(path))


def test_secret_files_must_be_private_regular_files(configured, tmp_path):
    path = tmp_path / "exposed"
    path.write_text("private")
    path.chmod(0o644)
    with pytest.raises(ManualVpsError, match="unsafe_private_file"):
        private_file(str(path))
    path.chmod(0o600)
    link = tmp_path / "linked"
    link.symlink_to(path)
    with pytest.raises(ManualVpsError):
        private_file(str(link))
    with pytest.raises(ManualVpsError):
        private_file("relative")


def test_cli_requires_explicit_apply_before_any_operation():
    from src.backend.manual_vps import main
    with pytest.raises(SystemExit) as result:
        main(["setup", "--node-file", "/does/not/exist"])
    assert result.value.code == 2
