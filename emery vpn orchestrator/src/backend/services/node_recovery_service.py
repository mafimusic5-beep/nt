from __future__ import annotations

import io
import json
import logging
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit

from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.common.config import settings
from src.common.models import VpnNode

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    detail: str
    host: str = ""
    port: int = 0


@dataclass(frozen=True, slots=True)
class RecoveryActionResult:
    ok: bool
    detail: str


class NodeProbe(Protocol):
    def probe(self, node: VpnNode) -> ProbeResult: ...


class NodeRecoveryTransport(Protocol):
    def restart_xray(self, node: VpnNode) -> RecoveryActionResult: ...

    def reboot_server(self, node: VpnNode) -> RecoveryActionResult: ...


class VlessTcpProbe:
    """Cheap liveness probe for every public VLESS listener in the pool."""

    @staticmethod
    def endpoint(node: VpnNode) -> tuple[str, int]:
        if node.provider in {"ionos_cloud", "manual_vps"} and node.endpoint:
            # IONOS nodes expose only the signed-device gateway; the VLESS
            # template and per-device ports are intentionally loopback-only.
            return node.endpoint, int(node.device_gate_port or 24443)
        for line in (node.config_payload or "").splitlines():
            candidate = line.strip()
            if not candidate.startswith("vless://"):
                continue
            try:
                parsed = urlsplit(candidate)
                if parsed.hostname:
                    return parsed.hostname, parsed.port or settings.firstvds_vless_port
            except ValueError:
                continue

        raw = (node.endpoint or "").strip()
        if not raw:
            return "", 0
        try:
            parsed = urlsplit(raw if "://" in raw else f"//{raw}")
            host = parsed.hostname or raw
            port = parsed.port or settings.firstvds_vless_port
            return host, port
        except ValueError:
            return raw, settings.firstvds_vless_port

    def probe(self, node: VpnNode) -> ProbeResult:
        host, port = self.endpoint(node)
        if not host or port <= 0:
            return ProbeResult(False, "missing_probe_endpoint", host, port)
        try:
            with socket.create_connection(
                (host, port),
                timeout=max(float(settings.recovery_probe_timeout_seconds), 0.2),
            ):
                return ProbeResult(True, "vless_tcp_open", host, port)
        except OSError as exc:
            return ProbeResult(False, f"vless_tcp_unreachable:{type(exc).__name__}", host, port)


class SshAndProviderRecoveryTransport:
    """Repair the same VPS through SSH, with a provider-script reboot fallback."""

    @staticmethod
    def _private_key_data(node: VpnNode) -> str:
        node_key = (node.ssh_private_key or "").strip()
        if node_key:
            return node_key
        key_path = (settings.recovery_ssh_private_key_path or "").strip()
        if not key_path:
            return ""
        try:
            return Path(key_path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @staticmethod
    def _load_private_key(private_key_data: str):
        try:
            import paramiko
        except ImportError:
            return None
        loaders = (
            getattr(paramiko, "RSAKey", None),
            getattr(paramiko, "Ed25519Key", None),
            getattr(paramiko, "ECDSAKey", None),
            getattr(paramiko, "DSSKey", None),
        )
        for loader in loaders:
            if loader is None:
                continue
            try:
                return loader.from_private_key(io.StringIO(private_key_data))
            except Exception:
                continue
        return None

    def _connect(self, node: VpnNode):
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("paramiko_not_installed") from exc

        key = self._load_private_key(self._private_key_data(node))
        if key is None:
            raise RuntimeError("node_ssh_private_key_unavailable")
        host, _ = VlessTcpProbe.endpoint(node)
        if not host:
            raise RuntimeError("node_ssh_endpoint_unavailable")

        client = paramiko.SSHClient()
        known_hosts = (settings.recovery_ssh_known_hosts_path or "").strip()
        pinned_host_key = (node.ssh_host_key or "").strip()
        if pinned_host_key:
            try:
                entry = paramiko.hostkeys.HostKeyEntry.from_line(
                    f"{host} {pinned_host_key}"
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("node_ssh_host_key_invalid") from exc
            if entry is None or entry.key is None:
                raise RuntimeError("node_ssh_host_key_invalid")
            client.get_host_keys().add(host, entry.key.get_name(), entry.key)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif known_hosts:
            if not Path(known_hosts).is_file():
                raise RuntimeError("ssh_known_hosts_file_missing")
            client.load_host_keys(known_hosts)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif settings.recovery_allow_unknown_host_keys:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            raise RuntimeError("ssh_host_key_verification_not_configured")

        try:
            client.connect(
                hostname=host,
                username=settings.recovery_ssh_user,
                pkey=key,
                timeout=max(int(settings.recovery_ssh_connect_timeout_seconds), 1),
                banner_timeout=max(int(settings.recovery_ssh_connect_timeout_seconds), 1),
                auth_timeout=max(int(settings.recovery_ssh_connect_timeout_seconds), 1),
                look_for_keys=False,
                allow_agent=False,
            )
        except Exception:
            client.close()
            raise
        return client

    @staticmethod
    def _command(client, command: str, *, timeout: int = 20) -> RecoveryActionResult:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="ignore").strip()
        err = stderr.read().decode(errors="ignore").strip()
        rc = stdout.channel.recv_exit_status()
        detail = (err or out or f"exit_{rc}")[:240]
        return RecoveryActionResult(rc == 0, detail)

    def restart_xray(self, node: VpnNode) -> RecoveryActionResult:
        client = None
        try:
            client = self._connect(node)
            result = self._command(
                client,
                ("systemctl restart xray emery-device-gate && systemctl is-active --quiet xray emery-device-gate"
                 if node.provider in {"ionos_cloud", "manual_vps"} else
                 "systemctl restart xray && systemctl is-active --quiet xray"),
            )
            return RecoveryActionResult(result.ok, f"ssh_restart_xray:{result.detail}")
        except Exception as exc:  # noqa: BLE001
            return RecoveryActionResult(False, f"ssh_restart_xray_failed:{type(exc).__name__}:{exc}")
        finally:
            if client is not None:
                client.close()

    def reboot_server(self, node: VpnNode) -> RecoveryActionResult:
        ssh_error = ""
        client = None
        try:
            client = self._connect(node)
            # Detach the reboot so an expected SSH disconnect is not treated as
            # failure. The subsequent VLESS probes are the source of truth.
            result = self._command(
                client,
                "nohup sh -c 'sleep 1; systemctl reboot' >/dev/null 2>&1 &",
                timeout=10,
            )
            if result.ok:
                return RecoveryActionResult(True, "ssh_reboot_requested")
            ssh_error = result.detail
        except Exception as exc:  # noqa: BLE001
            ssh_error = f"{type(exc).__name__}:{exc}"
        finally:
            if client is not None:
                client.close()

        if node.provider == "manual_vps":
            return RecoveryActionResult(False, "manual_vps_ssh_reboot_failed_provider_actions_disabled")
        script = (settings.recovery_provider_reboot_script or "").strip()
        if not script:
            return RecoveryActionResult(False, f"ssh_reboot_failed:{ssh_error};provider_script_missing")
        provider_server_id = (node.provider_server_id or node.firstvds_vps_id or "").strip()
        if not provider_server_id:
            return RecoveryActionResult(
                False,
                f"ssh_reboot_failed:{ssh_error};provider_server_id_missing",
            )
        payload = {
            "action": "reboot_existing_server",
            "node_id": node.id,
            "provider": node.provider,
            "provider_server_id": provider_server_id,
            "endpoint": node.endpoint,
            "region_code": node.region_code,
        }
        try:
            result = subprocess.run(
                [script, json.dumps(payload, ensure_ascii=False)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return RecoveryActionResult(False, f"provider_reboot_script_failed:{type(exc).__name__}")
        try:
            response = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            response = {}
        acknowledged_id = str(response.get("provider_server_id") or "").strip()
        ok = (
            result.returncode == 0
            and response.get("ok") is True
            and response.get("action") == "reboot_existing_server"
            and acknowledged_id == provider_server_id
        )
        detail = str(response.get("detail") or result.stderr or f"exit_{result.returncode}")[:240]
        return RecoveryActionResult(ok, f"provider_reboot:{detail}")


class NodeRecoveryService:
    """Probe and repair every active public node independently."""

    def __init__(
        self,
        db: Session,
        *,
        probe: NodeProbe | None = None,
        transport: NodeRecoveryTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.db = db
        self.audit = AuditRepository(db)
        self.probe = probe or VlessTcpProbe()
        self.transport = transport or SshAndProviderRecoveryTransport()
        self.sleeper = sleeper

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _monitored_nodes(self) -> list[VpnNode]:
        return self.db.scalars(
            select(VpnNode)
            .where(VpnNode.status == "active")
            .order_by(VpnNode.region_code.asc(), VpnNode.id.asc())
        ).all()

    def _lock_is_active(self, node: VpnNode, now: datetime) -> bool:
        lock_until = self._utc(node.recovery_lock_until)
        return bool(lock_until and lock_until > now)

    def _record_healthy(self, node_id: int, result: ProbeResult, now: datetime) -> None:
        node = self.db.get(VpnNode, node_id)
        if not node:
            return
        recovered = node.consecutive_health_failures > 0 or node.health_status != "healthy"
        node.consecutive_health_failures = 0
        node.health_status = "healthy"
        node.last_healthy_at = now
        if node.recovery_status not in {"restarting_xray", "rebooting_server"}:
            node.recovery_status = "idle"
            node.recovery_lock_until = None
        if recovered:
            self.audit.write(
                "system",
                "recovery-agent",
                "node_probe_recovered",
                "vpn_node",
                str(node.id),
                {"detail": result.detail, "host": result.host, "port": result.port},
            )
        self.db.commit()

    def _record_failure(self, node_id: int, result: ProbeResult, now: datetime) -> int | None:
        update_result = self.db.execute(
            update(VpnNode)
            .where(VpnNode.id == node_id, VpnNode.status == "active")
            .values(
                consecutive_health_failures=VpnNode.consecutive_health_failures + 1,
                # A node that did not answer must immediately disappear from
                # new admissions, even before the recovery threshold is met.
                health_status="down",
                recovery_status=case(
                    (
                        VpnNode.recovery_status.in_(
                            {"restarting_xray", "rebooting_server", "cooldown"}
                        ),
                        VpnNode.recovery_status,
                    ),
                    else_="suspect",
                ),
            )
        )
        if update_result.rowcount != 1:
            self.db.rollback()
            return None
        node = self.db.get(VpnNode, node_id, populate_existing=True)
        if not node:
            self.db.rollback()
            return None
        failures = int(node.consecutive_health_failures)
        self.audit.write(
            "system",
            "recovery-agent",
            "node_probe_failed",
            "vpn_node",
            str(node_id),
            {
                "failure_count": failures,
                "threshold": max(int(settings.recovery_failure_threshold), 1),
                "detail": result.detail,
                "host": result.host,
                "port": result.port,
                "ts": now.isoformat(),
            },
        )
        self.db.commit()
        return failures

    def _acquire_lock(self, node_id: int, now: datetime) -> bool:
        lock_until = now + timedelta(seconds=max(int(settings.recovery_lock_seconds), 60))
        result = self.db.execute(
            update(VpnNode)
            .where(
                VpnNode.id == node_id,
                VpnNode.status == "active",
                or_(VpnNode.recovery_lock_until.is_(None), VpnNode.recovery_lock_until <= now),
            )
            .values(
                recovery_lock_until=lock_until,
                recovery_status="restarting_xray",
                last_recovery_at=now,
                last_recovery_action="restart_xray",
                last_recovery_error="",
            )
            # SQLite returns timezone-aware columns as naive datetimes. Avoid
            # SQLAlchemy trying to re-evaluate this lease predicate in Python;
            # the database comparison is the atomic source of truth.
            .execution_options(synchronize_session=False)
        )
        self.db.commit()
        return result.rowcount == 1

    def _set_stage(self, node_id: int, status: str, action: str) -> VpnNode | None:
        node = self.db.get(VpnNode, node_id)
        if not node or node.status != "active":
            return None
        node.recovery_status = status
        node.last_recovery_action = action
        self.audit.write(
            "system",
            "recovery-agent",
            f"node_recovery_{action}_started",
            "vpn_node",
            str(node.id),
            {"region_code": node.region_code, "endpoint": node.endpoint},
        )
        self.db.commit()
        return node

    def _finish_success(self, node_id: int, action: str, result: ProbeResult) -> dict:
        now = datetime.now(timezone.utc)
        node = self.db.get(VpnNode, node_id)
        if not node:
            return {"node_id": node_id, "status": "missing_after_recovery"}
        node.health_status = "healthy"
        node.consecutive_health_failures = 0
        node.recovery_status = "idle"
        node.recovery_lock_until = None
        node.last_healthy_at = now
        node.last_recovery_at = now
        node.last_recovery_action = action
        node.last_recovery_error = ""
        self.audit.write(
            "system",
            "recovery-agent",
            "node_recovery_succeeded",
            "vpn_node",
            str(node.id),
            {"action": action, "probe": result.detail},
        )
        self.db.commit()
        return {"node_id": node.id, "status": "recovered", "action": action}

    def _finish_failure(self, node_id: int, action: str, detail: str) -> dict:
        now = datetime.now(timezone.utc)
        node = self.db.get(VpnNode, node_id)
        if not node:
            return {"node_id": node_id, "status": "missing_after_recovery"}
        node.health_status = "down"
        node.recovery_status = "cooldown"
        node.recovery_lock_until = now + timedelta(
            seconds=max(int(settings.recovery_cooldown_seconds), 30)
        )
        node.last_recovery_at = now
        node.last_recovery_action = action
        node.last_recovery_error = detail[:1000]
        self.audit.write(
            "system",
            "recovery-agent",
            "node_recovery_failed",
            "vpn_node",
            str(node.id),
            {"action": action, "detail": detail[:500]},
        )
        self.db.commit()
        return {"node_id": node.id, "status": "recovery_failed", "action": action, "detail": detail}

    def _recover(self, node_id: int) -> dict:
        node = self._set_stage(node_id, "restarting_xray", "restart_xray")
        if not node:
            return {"node_id": node_id, "status": "node_not_found"}

        restart = self.transport.restart_xray(node)
        if restart.ok:
            self.sleeper(max(int(settings.recovery_restart_grace_seconds), 0))
        probe_after_restart = self.probe.probe(node)
        if probe_after_restart.ok:
            return self._finish_success(node.id, "restart_xray", probe_after_restart)

        node = self._set_stage(node.id, "rebooting_server", "reboot_server")
        if not node:
            return {"node_id": node_id, "status": "node_not_found"}
        reboot = self.transport.reboot_server(node)
        if not reboot.ok:
            return self._finish_failure(
                node.id,
                "reboot_server",
                f"{restart.detail};{probe_after_restart.detail};{reboot.detail}",
            )

        self.sleeper(max(int(settings.recovery_reboot_grace_seconds), 0))
        attempts = max(int(settings.recovery_reboot_probe_attempts), 1)
        last_probe = ProbeResult(False, "reboot_probe_not_run")
        for attempt in range(attempts):
            last_probe = self.probe.probe(node)
            if last_probe.ok:
                return self._finish_success(node.id, "reboot_server", last_probe)
            if attempt + 1 < attempts:
                self.sleeper(max(int(settings.recovery_reboot_probe_interval_seconds), 1))

        return self._finish_failure(
            node.id,
            "reboot_server",
            f"{restart.detail};{reboot.detail};{last_probe.detail}",
        )

    def run_once(self) -> dict:
        results: list[dict] = []
        nodes = self._monitored_nodes()
        for node in nodes:
            results.append(self.run_node(node.id))

        return {"checked": len(nodes), "results": results}

    def run_node(self, node_id: int, *, now: datetime | None = None) -> dict:
        current_time = now or datetime.now(timezone.utc)
        node = self.db.get(VpnNode, node_id)
        if not node or node.status != "active":
            return {"node_id": node_id, "status": "not_public_active"}

        # A lease prevents duplicate repair commands, not health checks. Every
        # active public server is still probed on every cycle and can return to
        # the pool immediately if it becomes reachable during cooldown.
        probe = self.probe.probe(node)
        if probe.ok:
            self._record_healthy(node.id, probe, current_time)
            return {"node_id": node.id, "status": "healthy"}

        failures = self._record_failure(node.id, probe, current_time)
        if failures is None:
            return {"node_id": node_id, "status": "not_public_active"}
        self.db.refresh(node)
        if self._lock_is_active(node, current_time):
            return {
                "node_id": node.id,
                "status": "recovery_locked",
                "recovery_status": node.recovery_status,
                "failure_count": failures,
            }
        threshold = max(int(settings.recovery_failure_threshold), 1)
        if failures < threshold:
            return {
                "node_id": node.id,
                "status": "probe_failed",
                "failure_count": failures,
            }
        if not self._acquire_lock(node.id, current_time):
            return {"node_id": node.id, "status": "recovery_lock_contended"}

        try:
            return self._recover(node.id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("unhandled node recovery failure: node=%s", node.id)
            self.db.rollback()
            return self._finish_failure(
                node.id,
                "unhandled_exception",
                f"{type(exc).__name__}:{exc}",
            )
