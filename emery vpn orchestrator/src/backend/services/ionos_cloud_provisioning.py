"""Resumable, fail-closed IONOS provisioning. Each tick performs one bounded step.

Cloud POSTs are journalled before dispatch. An uncertain response is reconciled
by reading the unique operation name, never by blindly repeating a paid POST.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.services.ionos_cloud_api import IonosApiError, IonosCloudApi
from src.backend.services.ionos_cloud_bootstrap import IonosSshBootstrap, bundle_digest, cloud_init, initialize_ssh_keys
from src.backend.services.ionos_cloud_config import IonosConfigurationError, ordering_profile, public_ipv4, valid_uuid
from src.backend.services.provisioning_guard_service import ProvisioningGuardService
from src.common.config import settings
from src.common.models import IonosProvisionJob, VpnAssignment, VpnNode


class IonosCloudProvisioningService:
    def __init__(self, db: Session, *, api=None, bootstrap=None):
        self.db = db
        self.api = api or IonosCloudApi()
        self.bootstrap = bootstrap or IonosSshBootstrap()
        self.token = ""

    def _job(self, node: VpnNode, profile: dict) -> IonosProvisionJob:
        current = self.db.scalar(select(IonosProvisionJob).where(IonosProvisionJob.node_id == node.id))
        if current:
            return current
        if node.endpoint or node.config_payload or node.provider_server_id or node.ssh_private_key:
            raise IonosApiError("ionos_refusing_unowned_existing_node")
        operation = str(uuid.uuid4())
        name = f"skryon-{node.id}-{operation.replace('-', '')[:16]}"
        frozen = dict(profile, hostname=f"{name}.{profile['domain_suffix']}", bundle_sha256=bundle_digest())
        current = IonosProvisionJob(id=operation, node_id=node.id, resource_name=name,
                                    config_json=json.dumps(frozen, sort_keys=True))
        initialize_ssh_keys(node, current)
        self.db.add(current)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            current = self.db.scalar(select(IonosProvisionJob).where(IonosProvisionJob.node_id == node.id))
            if current is None:
                raise IonosApiError("ionos_job_conflict")
        return current

    def _claim(self, job: IonosProvisionJob) -> bool:
        now = datetime.now(timezone.utc)
        self.token = str(uuid.uuid4())
        try:
            self.db.execute(update(IonosProvisionJob).where(
                IonosProvisionJob.lease_until < now,
            ).values(lease_key=None, lease_token="", lease_until=None), execution_options={"synchronize_session": False})
            claimed = self.db.execute(update(IonosProvisionJob).where(
                IonosProvisionJob.id == job.id, IonosProvisionJob.lease_key.is_(None),
            ).values(lease_key="ionos_cloud", lease_token=self.token, lease_until=now + timedelta(minutes=5)),
                                      execution_options={"synchronize_session": False})
            self.db.commit()
            self.db.refresh(job)
            return claimed.rowcount == 1
        except IntegrityError:
            # The unique lease_key serializes paid steps across ALL regions and
            # workers, not just simultaneous requests for the same node.
            self.db.rollback()
            return False

    def _save(self, job: IonosProvisionJob) -> None:
        now = datetime.now(timezone.utc)
        with self.db.no_autoflush:
            fenced = self.db.execute(update(IonosProvisionJob).where(
                IonosProvisionJob.id == job.id, IonosProvisionJob.lease_token == self.token,
                IonosProvisionJob.lease_until > now,
            ).values(lease_until=now + timedelta(minutes=5)), execution_options={"synchronize_session": False})
        if fenced.rowcount != 1:
            self.db.rollback()
            raise IonosApiError("ionos_worker_lease_lost")
        self.db.commit()

    def _release(self, job: IonosProvisionJob) -> None:
        job_id = job.id
        # A lost/failed worker may have uncommitted ORM mutations. Releasing a
        # lease is never permission to flush them after another worker took over.
        self.db.rollback()
        with self.db.no_autoflush:
            released = self.db.execute(update(IonosProvisionJob).where(
                IonosProvisionJob.id == job_id, IonosProvisionJob.lease_token == self.token,
            ).values(lease_key=None, lease_token="", lease_until=None), execution_options={"synchronize_session": False})
        if released.rowcount == 1:
            self.db.commit()
        else:
            self.db.rollback()

    def _failure(self, job: IonosProvisionJob, node: VpnNode, detail: str) -> None:
        job_id, node_id = job.id, node.id
        self.db.rollback()
        job = self.db.get(IonosProvisionJob, job_id)
        node = self.db.get(VpnNode, node_id)
        if job is None or node is None:
            return
        job.last_error = detail[:128]
        node.health_status = "down"
        try:
            self._save(job)
        except IonosApiError:
            # Losing the lease must not overwrite the new owner's progress.
            self.db.rollback()

    def _owned_resource(self, job: IonosProvisionJob, operation: str, path: str,
                        name: str, payload: dict) -> dict | None:
        posted = set(json.loads(job.posted_operations))
        matches = [row for row in self.api.items(path) if row.get("properties", {}).get("name") == name]
        if len(matches) > 1:
            raise IonosApiError("ionos_ambiguous_resource_ownership")
        if matches:
            if operation not in posted:
                raise IonosApiError("ionos_resource_name_collision")
            return matches[0]
        if operation in posted:
            job.last_error = "ionos_post_outcome_unknown_reconciling"
            return None
        posted.add(operation)
        job.posted_operations = json.dumps(sorted(posted))
        self._save(job)  # Durable intent MUST precede the provider POST.
        try:
            return self.api.request("POST", path, payload=payload)
        except IonosApiError as exc:
            # Only an explicit rejection permits a future retry. Timeouts,
            # network errors, redirects and malformed successes keep the intent.
            if not exc.uncertain and exc.status_code in {400, 401, 403, 404, 422, 429}:
                posted.remove(operation)
                job.posted_operations = json.dumps(sorted(posted))
                self._save(job)
            raise

    @staticmethod
    def _available(resource: dict) -> bool:
        return resource.get("metadata", {}).get("state") == "AVAILABLE"

    @staticmethod
    def _firewall(profile: dict) -> list[dict]:
        return [
            {"properties": {"name": "management-ssh", "protocol": "TCP", "type": "INGRESS",
                             "sourceIp": profile["management_ipv4"], "portRangeStart": 22, "portRangeEnd": 22}},
            {"properties": {"name": "acme-http", "protocol": "TCP", "type": "INGRESS",
                             "portRangeStart": 80, "portRangeEnd": 80}},
            {"properties": {"name": "device-gate", "protocol": "TCP", "type": "INGRESS",
                             "portRangeStart": profile["gate_port"], "portRangeEnd": profile["gate_port"]}},
        ]

    def _server_payload(self, node: VpnNode, job: IonosProvisionJob, profile: dict) -> dict:
        return {
            "properties": {"name": job.resource_name + "-vpn", "type": profile["server_type"],
                           "cores": profile["cores"], "ram": profile["ram_mb"]},
            "entities": {
                "volumes": {"items": [{"properties": {
                    "name": job.resource_name + "-boot", "type": "SSD", "size": profile["disk_gb"],
                    "image": profile["image_id"], "sshKeys": [node.ssh_public_key],
                    "userData": cloud_init(node, job), "bus": "VIRTIO", "bootOrder": "PRIMARY",
                }}]},
                "nics": {"items": [{
                    "properties": {"name": job.resource_name + "-public", "lan": int(job.lan_id), "dhcp": True,
                                   "firewallActive": True, "firewallType": "INGRESS"},
                    "entities": {"firewallrules": {"items": self._firewall(profile)}},
                }]},
            },
        }

    def _network(self, job: IonosProvisionJob, profile: dict) -> str | None:
        server = self.api.request("GET", f"/datacenters/{valid_uuid(job.datacenter_id)}/servers/{valid_uuid(job.server_id)}",
                                  params={"depth": 5})
        props = server.get("properties", {})
        if props.get("name") != job.resource_name + "-vpn":
            raise IonosApiError("ionos_server_ownership_mismatch")
        if any(props.get(key) != value for key, value in {
            "cores": profile["cores"], "ram": profile["ram_mb"], "type": profile["server_type"],
        }.items()):
            raise IonosApiError("ionos_server_size_mismatch")
        if not self._available(server) or props.get("vmState") != "RUNNING":
            return None
        nics = server.get("entities", {}).get("nics", {}).get("items", [])
        if len(nics) != 1:
            raise IonosApiError("ionos_unexpected_network_interfaces")
        nic = nics[0]
        network = nic.get("properties", {})
        if network.get("name") != job.resource_name + "-public" or str(network.get("lan")) != job.lan_id:
            raise IonosApiError("ionos_network_ownership_mismatch")
        if network.get("firewallActive") is not True or network.get("firewallType", "INGRESS") != "INGRESS":
            raise IonosApiError("ionos_provider_firewall_not_enforced")
        rules = nic.get("entities", {}).get("firewallrules", {}).get("items", [])
        expected = {x["properties"]["name"]: x["properties"] for x in self._firewall(profile)}
        if len(rules) != len(expected):
            raise IonosApiError("ionos_provider_firewall_mismatch")
        for rule in rules:
            value = rule.get("properties", {})
            wanted = expected.pop(value.get("name"), None)
            if not wanted or any(value.get(k) != v for k, v in wanted.items()):
                raise IonosApiError("ionos_provider_firewall_mismatch")
            if value.get("sourceIp") and not wanted.get("sourceIp"):
                raise IonosApiError("ionos_public_gate_rule_restricted")
        addresses = network.get("ips", [])
        if len(addresses) != 1:
            raise IonosApiError("ionos_public_ip_missing_or_ambiguous")
        return public_ipv4(addresses[0])

    def _step(self, node: VpnNode, job: IonosProvisionJob, profile: dict) -> str:
        if job.phase == "created":
            self.api.preflight(profile)
            job.phase = "datacenter"
        elif job.phase == "datacenter":
            dc = self._owned_resource(job, "datacenter", "/datacenters", job.resource_name, {
                "properties": {"name": job.resource_name, "location": profile["location"],
                               "description": f"Skryon operation {job.id}; node {node.id}"},
            })
            if dc is None:
                return "pending"
            props = dc.get("properties", {})
            if props.get("description") != f"Skryon operation {job.id}; node {node.id}" or props.get("location") != profile["location"]:
                raise IonosApiError("ionos_datacenter_ownership_mismatch")
            job.datacenter_id = valid_uuid(dc.get("id"))
            if self._available(dc):
                job.phase = "lan"
        elif job.phase == "lan":
            lan = self._owned_resource(job, "lan", f"/datacenters/{valid_uuid(job.datacenter_id)}/lans", job.resource_name + "-public", {
                "properties": {"name": job.resource_name + "-public", "public": True},
            })
            if lan is None:
                return "pending"
            if lan.get("properties", {}).get("public") is not True or not re.fullmatch(r"[1-9][0-9]{0,8}", str(lan.get("id"))):
                raise IonosApiError("ionos_public_lan_invalid")
            job.lan_id = str(lan["id"])
            if self._available(lan):
                job.phase = "server"
        elif job.phase == "server":
            server = self._owned_resource(job, "server", f"/datacenters/{valid_uuid(job.datacenter_id)}/servers",
                                          job.resource_name + "-vpn", self._server_payload(node, job, profile))
            if server is None:
                return "pending"
            job.server_id = valid_uuid(server.get("id"))
            node.provider_server_id = job.datacenter_id + "/" + job.server_id
            node.contract_id = profile["contract_number"]
            job.phase = "network"
        elif job.phase == "network":
            address = self._network(job, profile)
            if address is None:
                return "pending"
            node.endpoint = address
            job.phase = "dns"
        elif job.phase == "dns":
            self.api.ensure_dns_record(zone_id=profile["dns_zone_id"], record_id=job.id,
                                       hostname=profile["hostname"], address=node.endpoint)
            job.phase = "bootstrap"
        elif job.phase == "bootstrap":
            # Starting a systemd unit is idempotent on this new, pinned host.
            # Losing an SSH response must not replace the VM or its SSH keys.
            self.bootstrap.start(node, job, profile)
            job.bootstrap_attempts += 1
            job.phase = "bootstrapping"
        elif job.phase == "bootstrapping":
            result = self.bootstrap.inspect(node, job)
            if result is None:
                return "pending"
            required = ("bootstrap_verified", "regional_policy_ready", "control_api_verified", "certificate_verified")
            if any(result.get(key) is not True for key in required):
                raise IonosApiError("ionos_readiness_attestation_missing")
            if result.get("hostname") != profile["hostname"] or result.get("endpoint") != node.endpoint:
                raise IonosApiError("ionos_readiness_endpoint_mismatch")
            pin = str(result.get("spki_sha256", ""))
            if not re.fullmatch(r"[0-9a-f]{64}", pin):
                raise IonosApiError("ionos_readiness_pin_invalid")
            from src.backend.services.node_adapters import FirstVdsBillManagerProvisioningService
            config = str(result.get("config_payload", ""))
            if not FirstVdsBillManagerProvisioningService.is_config_payload_valid(config):
                raise IonosApiError("ionos_readiness_config_invalid")
            from urllib.parse import urlsplit
            if urlsplit(config).hostname != node.endpoint:
                raise IonosApiError("ionos_readiness_config_endpoint_mismatch")
            node.config_payload = config
            node.device_gate_host = profile["hostname"]
            node.device_gate_server_name = profile["hostname"]
            node.device_gate_port = profile["gate_port"]
            node.device_gate_spki_sha256 = pin
            node.ssh_key_status = "installed"
            job.phase = "verify"
        elif job.phase == "verify":
            if self._network(job, profile) != node.endpoint:
                raise IonosApiError("ionos_final_network_not_ready")
            self.bootstrap.verify_data_plane(node, job, profile)
            node.status = "active"
            node.health_status = "healthy"
            node.load_score = 0
            node.provisioning_lock_key = None
            # Cloud is usage-billed, not a prepaid VPS contract. Do not invent
            # a paid-until date or let the old VPS renewal planner cancel it.
            node.paid_until = None
            node.renewal_status = "usage_billed"
            job.phase = "ready"
            job.bootstrap_host_private_key = ""  # No longer needed for creation.
            AuditRepository(self.db).write("system", "ionos", "ionos_node_ready", "vpn_node", str(node.id),
                                            {"operation_id": job.id, "provider_server_id": node.provider_server_id})
            return "ok"
        elif job.phase == "ready":
            return "ok"
        else:
            raise IonosApiError("ionos_unknown_provisioning_phase")
        return "pending"

    def advance(self, node_id: int) -> dict:
        node = self.db.get(VpnNode, node_id)
        if node is None or node.provider != "ionos_cloud":
            return {"node_id": node_id, "status": "blocked", "detail": "ionos_node_not_found"}
        if node.status == "active":
            return {"node_id": node_id, "status": "ok", "detail": "ionos_node_already_active"}
        if node.current_clients or self.db.scalar(select(VpnAssignment.id).where(VpnAssignment.node_id == node_id).limit(1)):
            return {"node_id": node_id, "status": "blocked", "detail": "ionos_existing_assignments_forbid_bootstrap"}
        all_nodes = list(self.db.scalars(select(VpnNode).where(VpnNode.id != node_id)))
        if settings.auto_provision_provider != "ionos_cloud":
            return {"node_id": node_id, "status": "blocked", "detail": "ionos_provider_not_selected"}
        decision = ProvisioningGuardService().evaluate(region_code=node.region_code, nodes=all_nodes)
        if not decision.allowed:
            return {"node_id": node_id, "status": "blocked", "detail": decision.reason}
        job = None
        claimed = False
        try:
            profile = ordering_profile(node.region_code)
            job = self._job(node, profile)
            snapshot = json.loads(job.config_json)
            if any(snapshot.get(key) != value for key, value in profile.items()) or snapshot["bundle_sha256"] != bundle_digest():
                raise IonosApiError("ionos_job_configuration_changed")
            claimed = self._claim(job)
            if not claimed:
                return {"node_id": node_id, "status": "pending", "detail": "ionos_controller_busy"}
            # Re-evaluate under the cross-region lease, before any provider call.
            self.db.expire_all()
            all_nodes = list(self.db.scalars(select(VpnNode).where(VpnNode.id != node_id)))
            decision = ProvisioningGuardService().evaluate(region_code=node.region_code, nodes=all_nodes)
            if not decision.allowed:
                raise IonosApiError(decision.reason)
            node.status = "provisioning"
            node.health_status = "unknown"
            node.provisioning_lock_key = node.region_code
            job.last_error = ""
            self._save(job)
            started = job.created_at.replace(tzinfo=timezone.utc) if job.created_at.tzinfo is None else job.created_at
            if datetime.now(timezone.utc) - started > timedelta(seconds=snapshot["bootstrap_timeout_seconds"] + 600):
                raise IonosApiError("ionos_provisioning_deadline_operator_review_required")
            outcome = self._step(node, job, snapshot)
            self._save(job)
            return {"node_id": node_id, "status": outcome, "detail": job.last_error or "ionos_" + job.phase}
        except (IonosApiError, IonosConfigurationError) as exc:
            detail = str(exc)
            if job is not None and claimed:
                self._failure(job, node, detail)
            return {"node_id": node_id, "status": "blocked", "detail": detail}
        except Exception as exc:
            # No provider body, bootstrap config, private key or traceback is
            # copied into public node responses/audit logs.
            if job is not None and claimed:
                self._failure(job, node, "ionos_step_failed_" + type(exc).__name__)
            return {"node_id": node_id, "status": "pending", "detail": "ionos_step_failed_" + type(exc).__name__}
        finally:
            if job is not None and claimed:
                self._release(job)
