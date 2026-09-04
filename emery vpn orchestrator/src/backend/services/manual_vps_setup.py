"""Resumable setup of explicitly registered VPSs. Never calls ordering APIs."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.services.ionos_cloud_api import IonosApiError
from src.backend.services.ionos_cloud_bootstrap import bundle_digest
from src.backend.services.manual_vps_bootstrap import ManualVpsBootstrap, helper_digest
from src.backend.services.manual_vps_config import (
    ManualVpsError, ManualVpsSpec, bootstrap_profile, require_neutral_public_name, setup_guard,
)
from src.common.config import settings
from src.common.models import Device, ManualVpsSetupJob, VpnAssignment, VpnNode

LEASE_SECONDS = 600


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _safe_error(exc: Exception) -> str:
    code = str(exc) if isinstance(exc, (ManualVpsError, IonosApiError)) else ""
    return code if re.fullmatch(r"(?:manual_vps|ionos)_[a-z0-9_]{1,100}", code) else "manual_vps_step_failed_" + type(exc).__name__


class ManualVpsSetupService:
    def __init__(self, db, *, bootstrap=None):
        self.db = db
        self.bootstrap = bootstrap or ManualVpsBootstrap()
        self.lease_token = ""

    @staticmethod
    def _profile(spec: ManualVpsSpec) -> dict:
        profile = bootstrap_profile()
        if spec.endpoint == profile["management_ipv4"]:
            raise ManualVpsError("manual_vps_refusing_management_server")
        require_neutral_public_name(spec.hostname, profile)
        return dict(profile, **spec.snapshot(), bundle_sha256=bundle_digest(), preflight_sha256=helper_digest())

    def _existing_host(self, spec: ManualVpsSpec) -> None:
        for node in self.db.scalars(select(VpnNode)):
            endpoint = (node.endpoint or "").strip()
            host = urlsplit(endpoint if "://" in endpoint else "//" + endpoint).hostname
            if host in {spec.endpoint, spec.hostname} or node.device_gate_host == spec.hostname:
                raise ManualVpsError("manual_vps_endpoint_already_registered")

    def check(self, spec: ManualVpsSpec) -> dict:
        """Local validation plus read-only DNS/SSH check; never a DB/remote write."""
        profile = self._profile(spec)
        self._existing_host(spec)
        node = VpnNode(id=1, endpoint=spec.endpoint, provider="manual_vps",
                       ssh_private_key=spec.ssh_private_key, ssh_host_key=spec.ssh_host_key)
        self.bootstrap.preflight(node, SimpleNamespace(id=str(uuid.uuid4())), profile)
        return {"status": "ok", "no_changes_made": True, "purchases_enabled": False,
                "endpoint": spec.endpoint, "hostname": spec.hostname}

    def register(self, spec: ManualVpsSpec) -> dict:
        profile = self._profile(spec)
        existing = self.db.scalar(select(ManualVpsSetupJob).where(
            or_(ManualVpsSetupJob.endpoint == spec.endpoint, ManualVpsSetupJob.hostname == spec.hostname),
        ))
        if existing:
            if json.loads(existing.config_json) != profile:
                raise ManualVpsError("manual_vps_existing_job_configuration_mismatch")
            return self.result(existing, "ok" if existing.phase == "ready" else "pending")
        self._existing_host(spec)
        # A draft has no usable config. It cannot receive devices before the
        # SSH installation and independent TLS/data-plane checks all succeed.
        node = VpnNode(
            name=spec.name, region_code=spec.region_code, endpoint=spec.endpoint,
            provider="manual_vps", status="provisioning", health_status="unknown",
            config_payload="", capacity_clients=spec.capacity_clients,
            bandwidth_limit_mbps=spec.bandwidth_limit_mbps,
            per_device_speed_limit_mbps=profile["per_device_speed_limit_mbps"],
            current_clients=0, ssh_private_key=spec.ssh_private_key,
            ssh_public_key=spec.ssh_public_key, ssh_host_key=spec.ssh_host_key,
            ssh_key_fingerprint=spec.ssh_key_fingerprint, ssh_key_status="provided",
            renewal_status="owner_managed", paid_until=None,
        )
        try:
            self.db.add(node)
            self.db.flush()
            job = ManualVpsSetupJob(
                id=str(uuid.uuid4()), node_id=node.id, endpoint=spec.endpoint, hostname=spec.hostname,
                config_json=json.dumps(profile, sort_keys=True),
                deadline_at=datetime.now(timezone.utc) + timedelta(seconds=profile["bootstrap_timeout_seconds"] + 600),
            )
            self.db.add(job)
            self.db.flush()
            # Perform the first read-only host check before committing the
            # registration. Unsafe targets leave neither a node nor a job.
            self.bootstrap.preflight(node, job, profile)
            AuditRepository(self.db).write(
                "admin", "local-cli", "manual_vps_setup_registered", "vpn_node", str(node.id),
                {"operation_id": job.id, "purchases_enabled": False},
            )
            self.db.commit()
            return self.result(job, "pending")
        except IntegrityError as exc:
            self.db.rollback()
            raise ManualVpsError("manual_vps_registration_conflict") from exc
        except Exception:
            self.db.rollback()
            raise

    @staticmethod
    def result(job, status: str) -> dict:
        return {"node_id": job.node_id, "operation_id": job.id, "status": status,
                "phase": job.phase, "detail": job.last_error, "purchases_enabled": False}

    def _claim(self, job) -> bool:
        now = datetime.now(timezone.utc)
        self.lease_token = str(uuid.uuid4())
        claimed = self.db.execute(update(ManualVpsSetupJob).where(
            ManualVpsSetupJob.id == job.id,
            or_(ManualVpsSetupJob.lease_until.is_(None), ManualVpsSetupJob.lease_until < now),
        ).values(lease_token=self.lease_token, lease_until=now + timedelta(seconds=LEASE_SECONDS)),
            execution_options={"synchronize_session": False})
        self.db.commit()
        self.db.refresh(job)
        return claimed.rowcount == 1

    def _save(self, job) -> None:
        now = datetime.now(timezone.utc)
        with self.db.no_autoflush:
            owned = self.db.execute(update(ManualVpsSetupJob).where(
                ManualVpsSetupJob.id == job.id, ManualVpsSetupJob.lease_token == self.lease_token,
                ManualVpsSetupJob.lease_until > now,
            ).values(lease_until=now + timedelta(seconds=LEASE_SECONDS)),
                execution_options={"synchronize_session": False})
        if owned.rowcount != 1:
            self.db.rollback()
            raise ManualVpsError("manual_vps_worker_lease_lost")
        self.db.commit()

    def _release(self, job_id: str) -> None:
        self.db.rollback()
        self.db.execute(update(ManualVpsSetupJob).where(
            ManualVpsSetupJob.id == job_id, ManualVpsSetupJob.lease_token == self.lease_token,
        ).values(lease_token="", lease_until=None), execution_options={"synchronize_session": False})
        self.db.commit()

    def _validate_job(self, node, job, profile, *, retry: bool = False) -> None:
        current = bootstrap_profile()
        if (any(profile.get(key) != value for key, value in current.items())
                or profile["bundle_sha256"] != bundle_digest() or profile["preflight_sha256"] != helper_digest()):
            raise ManualVpsError("manual_vps_job_configuration_changed")
        for key in ("endpoint", "name", "region_code", "capacity_clients", "bandwidth_limit_mbps",
                    "per_device_speed_limit_mbps", "ssh_public_key", "ssh_host_key"):
            if getattr(node, key) != profile[key]:
                raise ManualVpsError("manual_vps_node_identity_changed")
        if (node.endpoint != job.endpoint or job.hostname != profile["hostname"]
                or hashlib.sha256(node.ssh_private_key.encode()).hexdigest() != profile["ssh_key_sha256"]):
            raise ManualVpsError("manual_vps_node_identity_changed")
        if (node.current_clients or self.db.scalar(select(VpnAssignment.id).where(
                VpnAssignment.node_id == node.id).limit(1))
                or self.db.scalar(select(Device.id).where(Device.node_id == node.id).limit(1))):
            raise ManualVpsError("manual_vps_existing_devices_forbid_bootstrap")
        if node.status != "provisioning":
            raise ManualVpsError("manual_vps_node_not_in_setup_state")
        if not retry and datetime.now(timezone.utc) >= _utc(job.deadline_at):
            raise ManualVpsError("manual_vps_setup_deadline_requires_review")

    def _step(self, node, job, profile) -> str:
        if job.phase == "preflight":
            self.bootstrap.preflight(node, job, profile)
            job.phase = "bootstrap"
        elif job.phase == "bootstrap":
            self.bootstrap.start(node, job, profile)
            job.bootstrap_attempts += 1
            job.phase = "bootstrapping"
        elif job.phase == "bootstrapping":
            result = self.bootstrap.inspect(node, job)
            if result is None:
                return "pending"
            if (result.get("operation_id") != job.id
                    or result.get("hostname") != profile["hostname"] or result.get("endpoint") != node.endpoint
                    or any(result.get(key) is not True for key in (
                        "bootstrap_verified", "regional_policy_ready", "control_api_verified", "certificate_verified",
                        "public_metadata_hardened",
                    ))):
                raise ManualVpsError("manual_vps_readiness_attestation_invalid")
            pin, config = result.get("spki_sha256", ""), result.get("config_payload", "")
            from src.backend.services.node_adapters import FirstVdsBillManagerProvisioningService
            if (not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin)
                    or not isinstance(config, str)
                    or not FirstVdsBillManagerProvisioningService.is_config_payload_valid(config)
                    or urlsplit(config).hostname != node.endpoint):
                raise ManualVpsError("manual_vps_readiness_config_invalid")
            node.config_payload = config
            node.device_gate_host = profile["hostname"]
            node.device_gate_server_name = profile["hostname"]
            node.device_gate_port = profile["gate_port"]
            node.device_gate_spki_sha256 = pin
            node.ssh_key_status = "installed"
            job.phase = "verify"
        elif job.phase == "verify":
            self.bootstrap.verify_data_plane(node, job, profile)
            node.status = "active"
            node.health_status = "healthy"
            node.load_score = 0
            job.phase = "ready"
            AuditRepository(self.db).write(
                "system", "manual-vps", "manual_vps_node_ready", "vpn_node", str(node.id),
                {"operation_id": job.id, "purchases_enabled": False},
            )
            return "ok"
        else:
            raise ManualVpsError("manual_vps_unknown_setup_phase")
        return "pending"

    def advance(self, node_id: int, *, retry: bool = False) -> dict:
        job = self.db.scalar(select(ManualVpsSetupJob).where(ManualVpsSetupJob.node_id == node_id))
        node = self.db.get(VpnNode, node_id)
        if not job or not node or node.provider != "manual_vps":
            return {"node_id": node_id, "status": "blocked", "detail": "manual_vps_registered_job_required"}
        if job.phase == "ready":
            return self.result(job, "ok" if node.status == "active" else "blocked")
        if job.last_error and not retry:
            return self.result(job, "blocked")
        claimed = False
        job_id = job.id
        try:
            setup_guard()
            claimed = self._claim(job)
            if not claimed:
                return self.result(job, "pending")
            self.db.expire_all()
            # Recheck after claiming: another worker may have finished between
            # the initial read and acquisition of the lease.
            if job.phase == "ready":
                return self.result(job, "ok")
            if job.last_error and not retry:
                return self.result(job, "blocked")
            profile = json.loads(job.config_json)
            self._validate_job(node, job, profile, retry=retry)
            if retry:
                job.deadline_at = datetime.now(timezone.utc) + timedelta(seconds=profile["bootstrap_timeout_seconds"] + 600)
                AuditRepository(self.db).write(
                    "admin", "local-cli", "manual_vps_setup_retry", "vpn_node", str(node.id),
                    {"operation_id": job.id, "phase": job.phase},
                )
            job.last_error = ""
            self._save(job)
            outcome = self._step(node, job, profile)
            self._save(job)
            return self.result(job, outcome)
        except Exception as exc:
            detail = _safe_error(exc)
            self.db.rollback()
            if claimed:
                job = self.db.get(ManualVpsSetupJob, job_id)
                node = self.db.get(VpnNode, node_id)
                job.last_error = detail
                node.health_status = "down"
                try:
                    self._save(job)
                except ManualVpsError:
                    self.db.rollback()
            return {"node_id": node_id, "status": "blocked", "detail": detail, "purchases_enabled": False}
        finally:
            if claimed:
                self._release(job_id)

    def tick(self) -> dict:
        if not settings.manual_vps_setup_enabled:
            return {"status": "disabled"}
        try:
            setup_guard()
        except ManualVpsError as exc:
            return {"status": "blocked", "detail": str(exc)}
        # Only explicitly registered jobs. Never allocate, buy, or discover VPSs.
        node_id = self.db.scalar(select(ManualVpsSetupJob.node_id).where(
            ManualVpsSetupJob.phase != "ready", ManualVpsSetupJob.last_error == "",
        ).order_by(ManualVpsSetupJob.updated_at, ManualVpsSetupJob.created_at).limit(1))
        return self.advance(node_id) if node_id is not None else {"status": "idle"}

    def status(self) -> dict:
        return {"no_changes_made": True, "purchases_enabled": False, "jobs": [
            self.result(job, "ok" if job.phase == "ready" else "blocked" if job.last_error else "pending")
            for job in self.db.scalars(select(ManualVpsSetupJob).order_by(ManualVpsSetupJob.created_at))
        ]}
