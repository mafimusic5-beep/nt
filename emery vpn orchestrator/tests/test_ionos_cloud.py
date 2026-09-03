from __future__ import annotations

import base64
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.backend.repositories.node_repo import NodeRepository
from src.backend.services.ionos_cloud_api import CLOUD_BASE, DNS_BASE, IonosApiError, IonosCloudApi
from src.backend.services.ionos_cloud_bootstrap import IonosSshBootstrap, bundle_digest, cloud_init
from src.backend.services.ionos_cloud_config import IonosConfigurationError, ordering_profile
from src.backend.services.ionos_cloud_provisioning import IonosCloudProvisioningService
from src.backend.services.node_recovery_service import VlessTcpProbe
from src.backend.services.order_service import OrderService
from src.backend.services.provisioning_guard_service import ProvisioningGuardService
from src.backend.services.xray_credential_service import VlessDeviceConfigBuilder
from src.common.config import settings
from src.common.models import AuditLog, IonosProvisionJob, VpnAssignment, VpnNode

IMAGE_ID = "11111111-1111-4111-8111-111111111111"
ZONE_ID = "22222222-2222-4222-8222-222222222222"
PUBLIC_IP = "93.184.216.34"  # Fixture only: no test opens a socket to it.


@pytest.fixture
def enabled(monkeypatch):
    values = {
        "auto_provision_enabled": True, "ionos_cloud_apply_enabled": True,
        "auto_provision_provider": "ionos_cloud", "auto_provision_server_monthly_cost_eur": 25,
        "auto_provision_monthly_budget_eur": 200, "auto_provision_max_servers_per_hour": 10,
        "auto_provision_max_servers_per_day": 10, "auto_provision_retry_seconds": 300,
        "pool_accounting_bridge_enabled": True, "pool_bridge_api_key": "fixture-pool-key",
        "unique_device_credentials_enabled": True, "per_device_rate_limit_enforced": True,
        "smtp_abuse_protection_enabled": True, "device_bound_gate_enabled": True,
        "ionos_cloud_token": SecretStr("fixture-provider-secret"),
        "ionos_cloud_dns_token": SecretStr("fixture-dns-secret"),
        "ionos_cloud_contract_number": "123456",
        "ionos_cloud_management_ipv4": "9.9.9.9", "ionos_cloud_dns_zone_id": ZONE_ID,
        "ionos_cloud_domain_suffix": "vpn.example.com",
        "ionos_cloud_gate_authorize_url": "https://control.example.com/internal/device-gate/authorize",
        "ionos_cloud_gate_authorize_key": SecretStr("fixture_control_key_" + "a" * 32),
        "ionos_cloud_acme_email": "operator@example.com", "ionos_cloud_acme_terms_accepted": True,
        "ionos_cloud_xray_version": "26.8.1", "ionos_cloud_xray_sha256": "a" * 64,
        "ionos_cloud_reality_server_name": "example.com", "ionos_cloud_probe_url": "https://example.com/",
        "ionos_cloud_bootstrap_timeout_seconds": 7200, "recovery_ssh_user": "root",
        "xray_config_path": "/usr/local/etc/xray/config.json", "xray_credential_script": "",
        "device_gate_service_name": "emery-device-gate", "xray_client_port_start": 20000,
        "xray_client_port_end": 20199, "regional_policy_sync_script": "/opt/emery/regional-policy/regional_policy.py",
    }
    profile = {"location": "de/fra", "image_id": IMAGE_ID, "cores": 2, "ram_mb": 4096, "disk_gb": 20}
    values["ionos_cloud_region_profiles_json"] = json.dumps({"de": profile, "nl": profile})
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)
    return values


def add_node(db, region="de"):
    node = VpnNode(name="new-cloud-node", region_code=region, provider="ionos_cloud", status="draft",
                   endpoint="", config_payload="", health_status="unknown", current_clients=0,
                   provisioning_lock_key=region)
    db.add(node)
    db.commit()
    return node


class FakeApi:
    """Only an in-memory provider. All writes and uncertain responses are observable."""
    def __init__(self):
        self.collections = {}
        self.posts = []
        self.dns = []
        self.preflights = 0
        self.server_loss = ""
        self.server_rejection = False

    def preflight(self, profile):
        self.preflights += 1

    def items(self, path, **kwargs):
        return copy.deepcopy(self.collections.get(path, []))

    def request(self, method, path, *, payload=None, **kwargs):
        if method == "POST":
            self.posts.append((path, copy.deepcopy(payload)))
            is_server = path.endswith("/servers")
            if is_server and self.server_rejection:
                self.server_rejection = False
                raise IonosApiError("ionos_http_400", 400)
            if is_server and self.server_loss == "before":
                self.server_loss = ""
                raise IonosApiError("ionos_transport_error", uncertain=True)
            result = copy.deepcopy(payload)
            result["id"] = "1" if path.endswith("/lans") else str(uuid.uuid4())
            result["metadata"] = {"state": "AVAILABLE"}
            if is_server:
                result["properties"]["vmState"] = "RUNNING"
                result["entities"]["nics"]["items"][0]["properties"]["ips"] = [PUBLIC_IP]
            self.collections.setdefault(path, []).append(result)
            if is_server and self.server_loss == "after":
                self.server_loss = ""
                raise IonosApiError("ionos_transport_error", uncertain=True)
            return copy.deepcopy(result)
        assert method == "GET"
        parent, resource_id = path.rsplit("/", 1)
        return copy.deepcopy(next(row for row in self.collections[parent] if row["id"] == resource_id))

    def ensure_dns_record(self, **kwargs):
        self.dns.append(kwargs)


class FakeBootstrap:
    def __init__(self):
        self.starts = 0
        self.checks = 0
        self.ready = True
        self.bad_attestation = ""
        self.fail_verification = False

    def start(self, node, job, profile):
        assert node.ssh_host_key.startswith("ssh-ed25519 ")
        self.starts += 1

    def inspect(self, node, job):
        if not self.ready:
            return None
        profile = json.loads(job.config_json)
        result = {
            "operation_id": job.id, "hostname": profile["hostname"], "endpoint": node.endpoint,
            "bootstrap_verified": True, "regional_policy_ready": True, "control_api_verified": True,
            "certificate_verified": True, "spki_sha256": "b" * 64,
            "config_payload": f"vless://{IMAGE_ID}@{node.endpoint}:443?type=tcp&security=reality&pbk={'a' * 43}&sid=0123456789abcdef&flow=xtls-rprx-vision",
        }
        if self.bad_attestation:
            result[self.bad_attestation] = False
        return result

    def verify_data_plane(self, node, job, profile):
        assert node.status == "provisioning"
        self.checks += 1
        if self.fail_verification:
            raise IonosApiError("ionos_vpn_canary_failed")


def advance_to(service, db, node, phase):
    for _ in range(15):
        job = db.scalar(select(IonosProvisionJob).where(IonosProvisionJob.node_id == node.id))
        if job and job.phase == phase:
            return job
        result = service.advance(node.id)
        assert result["status"] in {"pending", "ok"}, result
    pytest.fail("phase was not reached")


def test_complete_pipeline_only_publishes_after_independent_verification(enabled, db_session):
    node = add_node(db_session)
    api, bootstrap = FakeApi(), FakeBootstrap()
    service = IonosCloudProvisioningService(db_session, api=api, bootstrap=bootstrap)
    job = advance_to(service, db_session, node, "verify")
    assert node.status == "provisioning"
    assert NodeRepository(db_session).best_active_node("de") is None
    assert len(api.posts) == 3
    assert bootstrap.starts == 1
    assert service.advance(node.id)["status"] == "ok"
    assert node.status == "active" and node.health_status == "healthy"
    assert NodeRepository(db_session).best_active_node("de").id == node.id
    assert bootstrap.checks == 1 and job.phase == "ready"
    assert job.bootstrap_host_private_key == "" and node.paid_until is None
    assert node.renewal_status == "usage_billed"
    assert job.lease_key is None and node.provisioning_lock_key is None
    assert service.advance(node.id)["status"] == "ok" and len(api.posts) == 3
    assert "fixture" not in " ".join(db_session.scalars(select(AuditLog.details)))
    assignment = VpnAssignment(id=11, node_id=node.id, client_uuid=IMAGE_ID, client_port=20000)
    uri = VlessDeviceConfigBuilder.build(node, assignment)
    assert "@127.0.0.1:" in uri and "eg_spki=" in uri and "flow=xtls-rprx-vision" in uri
    assert "fixture-provider-secret" not in uri and "fixture_control_key" not in uri


def test_userdata_only_contains_this_nodes_keys_not_provider_or_control_secrets(enabled, db_session):
    node = add_node(db_session)
    service = IonosCloudProvisioningService(db_session, api=FakeApi(), bootstrap=FakeBootstrap())
    job = advance_to(service, db_session, node, "datacenter")
    decoded = base64.b64decode(cloud_init(node, job)).decode()
    config = json.loads(decoded.removeprefix("#cloud-config\n"))
    assert config["ssh_keys"]["ed25519_public"] == node.ssh_host_key
    assert config["users"][0]["ssh_authorized_keys"] == [node.ssh_public_key]
    assert config["ssh_pwauth"] is False
    assert node.ssh_private_key not in decoded
    assert "fixture-provider-secret" not in decoded and "fixture_control_key" not in decoded
    assert "fixture-provider-secret" not in job.config_json


@pytest.mark.parametrize("flag", ["auto_provision_enabled", "ionos_cloud_apply_enabled", "device_bound_gate_enabled",
                                    "unique_device_credentials_enabled", "per_device_rate_limit_enforced",
                                    "smtp_abuse_protection_enabled", "pool_accounting_bridge_enabled"])
def test_any_missing_safety_switch_prevents_all_api_calls(enabled, monkeypatch, db_session, flag):
    monkeypatch.setattr(settings, flag, False)
    node, api = add_node(db_session), FakeApi()
    result = IonosCloudProvisioningService(db_session, api=api, bootstrap=FakeBootstrap()).advance(node.id)
    assert result["status"] == "blocked" and not api.posts and api.preflights == 0
    assert db_session.scalar(select(IonosProvisionJob)) is None


@pytest.mark.parametrize("key,value", [
    ("ionos_cloud_management_ipv4", "0.0.0.0/0"), ("ionos_cloud_management_ipv4", "127.0.0.1"),
    ("ionos_cloud_gate_authorize_url", "http://control.example.com/internal/device-gate/authorize"),
    ("ionos_cloud_gate_authorize_url", "https://user:secret@control.example.com/internal/device-gate/authorize"),
    ("ionos_cloud_gate_authorize_url", "https://control.example.com/internal/device-gate/authorize?key=secret"),
    ("ionos_cloud_gate_authorize_key", SecretStr("a" * 32 + "\nOTHER=1")),
    ("ionos_cloud_acme_terms_accepted", False), ("ionos_cloud_xray_sha256", "latest"),
    ("ionos_cloud_xray_version", "latest"), ("ionos_cloud_dns_zone_id", "../other"),
    ("ionos_cloud_domain_suffix", "evil.example;whoami"), ("ionos_cloud_region_profiles_json", "{}"),
    ("ionos_cloud_bootstrap_timeout_seconds", 0), ("regional_policy_sync_script", ""),
])
def test_invalid_operator_configuration_fails_locally(enabled, monkeypatch, key, value):
    monkeypatch.setattr(settings, key, value)
    with pytest.raises(IonosConfigurationError):
        ordering_profile("de")


def test_zero_budget_does_not_mean_unlimited(enabled, monkeypatch):
    monkeypatch.setattr(settings, "auto_provision_monthly_budget_eur", 0)
    assert ProvisioningGuardService().evaluate(region_code="de", nodes=[]).reason == "auto_provision_budget_unconfigured"


def test_uncertain_server_post_is_reconciled_not_repeated(enabled, db_session):
    node, api = add_node(db_session), FakeApi()
    service = IonosCloudProvisioningService(db_session, api=api, bootstrap=FakeBootstrap())
    job = advance_to(service, db_session, node, "server")
    api.server_loss = "after"
    assert service.advance(node.id)["detail"] == "ionos_transport_error"
    assert "server" in json.loads(job.posted_operations) and job.phase == "server"
    advance_to(service, db_session, node, "ready")
    assert sum(path.endswith("/servers") for path, _ in api.posts) == 1


def test_unknown_post_without_visible_resource_never_orders_again(enabled, db_session):
    node, api = add_node(db_session), FakeApi()
    service = IonosCloudProvisioningService(db_session, api=api, bootstrap=FakeBootstrap())
    job = advance_to(service, db_session, node, "server")
    api.server_loss = "before"
    service.advance(node.id)
    for _ in range(4):
        assert service.advance(node.id)["detail"] == "ionos_post_outcome_unknown_reconciling"
    assert job.phase == "server" and node.status != "active"
    assert sum(path.endswith("/servers") for path, _ in api.posts) == 1


def test_explicit_provider_rejection_allows_safe_retry(enabled, db_session):
    node, api = add_node(db_session), FakeApi()
    service = IonosCloudProvisioningService(db_session, api=api, bootstrap=FakeBootstrap())
    job = advance_to(service, db_session, node, "server")
    api.server_rejection = True
    service.advance(node.id)
    assert "server" not in json.loads(job.posted_operations)
    advance_to(service, db_session, node, "ready")
    assert sum(len(rows) for path, rows in api.collections.items() if path.endswith("/servers")) == 1


@pytest.mark.parametrize("field", ["bootstrap_verified", "regional_policy_ready", "control_api_verified", "certificate_verified"])
def test_missing_attestation_keeps_node_out_of_pool(enabled, db_session, field):
    node, api, bootstrap = add_node(db_session), FakeApi(), FakeBootstrap()
    service = IonosCloudProvisioningService(db_session, api=api, bootstrap=bootstrap)
    job = advance_to(service, db_session, node, "bootstrapping")
    bootstrap.bad_attestation = field
    assert service.advance(node.id)["detail"] == "ionos_readiness_attestation_missing"
    assert job.phase == "bootstrapping" and node.status != "active" and bootstrap.checks == 0


def test_failed_canary_retries_same_server_never_publishes(enabled, db_session):
    node, api, bootstrap = add_node(db_session), FakeApi(), FakeBootstrap()
    service = IonosCloudProvisioningService(db_session, api=api, bootstrap=bootstrap)
    job = advance_to(service, db_session, node, "verify")
    bootstrap.fail_verification = True
    for _ in range(3):
        assert service.advance(node.id)["detail"] == "ionos_vpn_canary_failed"
    assert node.status == "provisioning" and job.phase == "verify" and len(api.posts) == 3
    bootstrap.fail_verification = False
    assert service.advance(node.id)["status"] == "ok"


def test_wrong_public_firewall_never_reaches_bootstrap(enabled, db_session):
    node, api, bootstrap = add_node(db_session), FakeApi(), FakeBootstrap()
    service = IonosCloudProvisioningService(db_session, api=api, bootstrap=bootstrap)
    job = advance_to(service, db_session, node, "network")
    server = api.collections[f"/datacenters/{job.datacenter_id}/servers"][0]
    server["entities"]["nics"]["items"][0]["properties"]["firewallActive"] = False
    assert service.advance(node.id)["detail"] == "ionos_provider_firewall_not_enforced"
    assert bootstrap.starts == 0 and node.status != "active"


def test_configuration_cannot_change_mid_purchase(enabled, db_session, monkeypatch):
    node, api = add_node(db_session), FakeApi()
    service = IonosCloudProvisioningService(db_session, api=api, bootstrap=FakeBootstrap())
    advance_to(service, db_session, node, "server")
    monkeypatch.setattr(settings, "ionos_cloud_contract_number", "654321")
    assert service.advance(node.id)["detail"] == "ionos_job_configuration_changed"
    assert len(api.posts) == 2


def test_existing_server_or_assignments_cannot_be_bootstrapped(enabled, db_session):
    node, api = add_node(db_session), FakeApi()
    node.endpoint = PUBLIC_IP
    db_session.commit()
    service = IonosCloudProvisioningService(db_session, api=api, bootstrap=FakeBootstrap())
    assert service.advance(node.id)["detail"] == "ionos_refusing_unowned_existing_node"
    node.current_clients = 1
    db_session.commit()
    assert service.advance(node.id)["detail"] == "ionos_existing_assignments_forbid_bootstrap"
    assert api.preflights == 0 and not api.posts


def test_lease_serializes_regions_and_fences_expired_worker(enabled, db_session):
    node1, node2 = add_node(db_session), add_node(db_session, "nl")
    one = IonosCloudProvisioningService(db_session, api=FakeApi(), bootstrap=FakeBootstrap())
    job1 = one._job(node1, ordering_profile("de"))
    job2 = one._job(node2, ordering_profile("nl"))
    assert one._claim(job1)
    with Session(db_session.get_bind(), autoflush=False) as other_db:
        two = IonosCloudProvisioningService(other_db, api=FakeApi(), bootstrap=FakeBootstrap())
        other_job = other_db.get(IonosProvisionJob, job2.id)
        assert not two._claim(other_job)
        other_db.execute(update(IonosProvisionJob).where(IonosProvisionJob.id == job1.id).values(
            lease_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        other_db.commit()
        assert two._claim(other_job)
        node1.status = "active"  # stale worker tries to publish after losing its lease
        with pytest.raises(IonosApiError, match="lease_lost"):
            one._save(job1)
        one._release(job1)
        other_db.expire_all()
        assert other_db.get(IonosProvisionJob, job2.id).lease_token == two.token
        assert other_db.get(VpnNode, node1.id).status == "draft"
        two._release(other_job)


def test_progress_cooldown_uses_job_not_healthcheck_timestamp(enabled, db_session, monkeypatch):
    node = add_node(db_session)
    service = IonosCloudProvisioningService(db_session, api=FakeApi(), bootstrap=FakeBootstrap())
    job = advance_to(service, db_session, node, "datacenter")
    job.updated_at = datetime.now(timezone.utc) - timedelta(seconds=40)
    node.updated_at = datetime.now(timezone.utc)
    db_session.commit()
    orders = OrderService(db_session)
    calls = []
    monkeypatch.setattr(orders.node_orchestrator, "provision_node", lambda node_id: calls.append(node_id) or {"status": "pending"})
    assert orders.ensure_capacity_allocation()["status"] == "auto_provision_retried"
    assert calls == [node.id]


def test_ionos_recovery_probes_gateway_not_private_template():
    node = VpnNode(provider="ionos_cloud", endpoint=PUBLIC_IP, device_gate_port=24443,
                   config_payload=f"vless://{IMAGE_ID}@{PUBLIC_IP}:443")
    assert VlessTcpProbe.endpoint(node) == (PUBLIC_IP, 24443)


def test_api_sends_tokens_only_to_fixed_provider_hosts(enabled):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"items": []})
    api = IonosCloudApi(transport=httpx.MockTransport(handler))
    api.items("/datacenters")
    api.items("/zones", dns=True)
    assert str(requests[0].url).startswith(CLOUD_BASE + "/datacenters?")
    assert requests[0].headers["Authorization"] == "Bearer fixture-provider-secret"
    assert requests[0].headers["X-Contract-Number"] == "123456"
    assert str(requests[1].url).startswith(DNS_BASE + "/zones?")
    assert requests[1].headers["Authorization"] == "Bearer fixture-dns-secret"
    assert "X-Contract-Number" not in requests[1].headers


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 500, 503])
def test_api_never_follows_redirects_or_exposes_response_secrets(enabled, status):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="fixture-secret-private-key", headers={"Location": "https://untrusted.example/"})
    api = IonosCloudApi(transport=httpx.MockTransport(handler))
    with pytest.raises(IonosApiError) as raised:
        api.request("POST", "/datacenters", payload={})
    assert len(requests) == 1 and "fixture-secret" not in str(raised.value)


@pytest.mark.parametrize("method,path", [("DELETE", "/datacenters"), ("GET", "https://untrusted.example/"),
                                           ("PUT", "/zones/../other"), ("GET", "/zones?token=other")])
def test_api_rejects_unowned_paths_before_network(enabled, method, path):
    api = IonosCloudApi(transport=httpx.MockTransport(lambda _: pytest.fail("network must not be called")))
    with pytest.raises(IonosApiError, match="request_not_allowed"):
        api.request(method, path)


def test_dns_relative_name_and_idempotent_put(enabled):
    record_id, records, writes = str(uuid.uuid4()), {}, []
    hostname = "node.vpn.example.com"
    def handler(request):
        if request.url.path == f"/zones/{ZONE_ID}":
            return httpx.Response(200, json={"properties": {"name": "example.com", "enabled": True}})
        if request.url.path.endswith("/records"):
            return httpx.Response(200, json={"items": list(records.values())})
        if request.method == "GET":
            return httpx.Response(200, json=records[record_id]) if record_id in records else httpx.Response(404)
        assert request.method == "PUT" and request.url.path.endswith(record_id)
        value = json.loads(request.content)
        assert value["properties"]["name"] == "node.vpn"
        writes.append(value)
        value["metadata"] = {"fqdn": hostname}
        records[record_id] = value
        return httpx.Response(201, json=value)
    api = IonosCloudApi(transport=httpx.MockTransport(handler))
    for _ in range(2):
        api.ensure_dns_record(zone_id=ZONE_ID, record_id=record_id, hostname=hostname, address=PUBLIC_IP)
    assert len(records) == 1 and len(writes) == 2


def test_dns_never_overwrites_an_existing_hostname(enabled):
    def handler(request):
        if request.url.path == f"/zones/{ZONE_ID}":
            return httpx.Response(200, json={"properties": {"name": "example.com", "enabled": True}})
        assert request.method == "GET"
        if request.url.path.endswith("/records"):
            return httpx.Response(200, json={"items": [{"properties": {"name": "node.vpn", "content": PUBLIC_IP}}]})
        return httpx.Response(404)
    with pytest.raises(IonosApiError, match="already_exists"):
        IonosCloudApi(transport=httpx.MockTransport(handler)).ensure_dns_record(
            zone_id=ZONE_ID, record_id=str(uuid.uuid4()), hostname="node.vpn.example.com", address=PUBLIC_IP)


@pytest.mark.parametrize("changes", [{"public": False}, {"cloudInit": "NONE"}, {"name": "Windows"},
                                      {"location": "us/las"}, {"size": 999}])
def test_image_preflight_rejects_incompatible_image(enabled, changes):
    props = dict(licenceType="LINUX", public=True, cloudInit="V1", name="Debian 12", location="de/fra", size=2)
    props.update(changes)
    api = IonosCloudApi(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"properties": props})))
    with pytest.raises(IonosApiError):
        api.preflight(ordering_profile("de"))


@pytest.mark.parametrize("payload", [b"not-json", b"[]", b"x" * (4 * 1024 * 1024 + 1)])
def test_bad_successful_post_response_is_uncertain_and_secret_free(enabled, payload):
    api = IonosCloudApi(transport=httpx.MockTransport(lambda _: httpx.Response(201, content=payload)))
    with pytest.raises(IonosApiError) as raised:
        api.request("POST", "/datacenters", payload={})
    assert raised.value.uncertain is True


@pytest.mark.parametrize("state", ["active", "activating"])
def test_running_bootstrap_is_never_uploaded_or_started_twice(enabled, monkeypatch, state):
    runner = IonosSshBootstrap()
    client, sftp = MagicMock(), MagicMock()
    client.open_sftp.return_value.__enter__.return_value = sftp
    sftp.lstat.side_effect = FileNotFoundError()
    monkeypatch.setattr(runner.ssh, "_connect", lambda _: client)
    command = MagicMock(return_value=state)
    monkeypatch.setattr(runner, "_exec", command)
    profile = dict(ordering_profile("de"), bundle_sha256=bundle_digest())
    runner.start(VpnNode(id=7), SimpleNamespace(id=str(uuid.uuid4())), profile)
    assert command.call_count == 1
    sftp.file.assert_not_called()
    client.close.assert_called_once()


def test_bootstrap_start_is_nonblocking_and_survives_reboot(enabled, monkeypatch):
    runner = IonosSshBootstrap()
    client, sftp = MagicMock(), MagicMock()
    client.open_sftp.return_value.__enter__.return_value = sftp
    sftp.lstat.side_effect = FileNotFoundError()
    monkeypatch.setattr(runner.ssh, "_connect", lambda _: client)
    commands = []
    monkeypatch.setattr(runner, "_exec", lambda _client, command: commands.append(command) or "inactive")
    profile = dict(ordering_profile("de"), bundle_sha256=bundle_digest(), hostname="node.vpn.example.com")
    runner.start(VpnNode(id=7, endpoint=PUBLIC_IP), SimpleNamespace(id=str(uuid.uuid4())), profile)
    assert "start --no-block emery-ionos-bootstrap.service" in commands[-1]
    assert "enable emery-ionos-bootstrap.service" in commands[-1]
    assert all("fixture-provider-secret" not in command and "fixture_control_key" not in command for command in commands)
    writes = [call.args[0] for call in sftp.file.return_value.__enter__.return_value.write.call_args_list]
    unit = next(value for value in writes if isinstance(value, str) and value.startswith("[Unit]"))
    assert "ConditionPathExists=!/var/lib/emery-ionos/ready.json" in unit
    assert "TimeoutStartSec=7200" in unit


def test_cli_plan_and_status_never_expose_credentials_or_call_provider(enabled, db_session, monkeypatch):
    from src.backend.ionos_cloud import plan, status
    node = add_node(db_session)
    service = IonosCloudProvisioningService(db_session, api=FakeApi(), bootstrap=FakeBootstrap())
    advance_to(service, db_session, node, "datacenter")
    monkeypatch.setattr(IonosCloudApi, "request", lambda *a, **kw: pytest.fail("read-only local plan must not call API"))
    report = json.dumps([plan(db_session, "de"), status(db_session)])
    assert "fixture-provider-secret" not in report and "fixture_control_key" not in report
    assert "PRIVATE KEY" not in report and "ssh_private_key" not in report
    assert "no_changes_made" in report
