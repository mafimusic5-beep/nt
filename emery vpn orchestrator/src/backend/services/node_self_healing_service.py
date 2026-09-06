from __future__ import annotations

import io
import logging
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.node_repo import NodeRepository
from src.common.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _SelfHealingState:
    failure_streaks: dict[int, int] = field(default_factory=dict)
    recovery_attempts: dict[int, int] = field(default_factory=dict)
    last_recovery_at: dict[int, float] = field(default_factory=dict)


_state = _SelfHealingState()


def reset_self_healing_state() -> None:
    """Clear in-process recovery counters. Primarily useful for tests."""
    _state.failure_streaks.clear()
    _state.recovery_attempts.clear()
    _state.last_recovery_at.clear()


class NodeSelfHealingService:
    """Observe every node health result and recover unhealthy VPN nodes safely.

    Recovery is deliberately staged:
    1. Remove an unhealthy active node from the connectable pool immediately.
    2. Require several consecutive failed healthchecks before changing anything.
    3. Try to restart Xray over SSH without changing VPN keys/configuration.
    4. If the host/service is still unreachable, reboot the VPS through the
       provider API. Repeated failed recovery cycles escalate to a hard reboot.
    5. Return the node to ``active`` only after a healthy healthcheck.

    Counters are process-local so no database migration is required. Cooldowns
    and a retry window protect against reboot loops.
    """

    def __init__(self, db: Session, provisioning: Any) -> None:
        self.db = db
        self.repo = NodeRepository(db)
        self.audit = AuditRepository(db)
        self.provisioning = provisioning

    def process(self, healthcheck_result: dict) -> dict:
        if not settings.health_self_heal_enabled:
            return {"enabled": False, "actions": []}

        actions: list[dict] = []
        for row in healthcheck_result.get("results", []):
            node_id = row.get("node_id")
            if not isinstance(node_id, int):
                continue
            node = self.repo.get_node(node_id)
            if node is None:
                continue

            event = self._observe_node(node, row)
            if event:
                actions.append(event)

        if actions:
            self.db.commit()
        return {"enabled": True, "actions": actions}

    def _observe_node(self, node: Any, row: dict) -> dict | None:
        node_id = int(node.id)
        health = str(row.get("health_status") or "unknown")

        if health == "healthy":
            had_recovery_state = (
                node.status == "recovering"
                or node_id in _state.failure_streaks
                or node_id in _state.recovery_attempts
            )
            self._clear_node_state(node_id)
            if node.status == "recovering":
                node.status = "active"
            if had_recovery_state:
                event = {
                    "node_id": node_id,
                    "status": "recovered",
                    "action": "healthcheck_confirmed",
                    "health_status": "healthy",
                }
                row["self_heal"] = event
                self._audit(node_id, "node_self_heal_recovered", event)
                logger.info("self-heal recovered node=%s", node_id)
                return event
            return None

        # Do not touch nodes intentionally disabled by an operator or lifecycle job.
        if node.status not in {"active", "recovering"}:
            return None
        if health not in {"down", "degraded"}:
            return None

        # First bad observation removes the node from every pool immediately.
        node.status = "recovering"
        streak = _state.failure_streaks.get(node_id, 0) + 1
        _state.failure_streaks[node_id] = streak
        row["self_heal_state"] = "observing"
        row["self_heal_failure_streak"] = streak

        threshold = max(int(settings.health_self_heal_failure_threshold), 1)
        if streak < threshold:
            return None

        now = time.monotonic()
        last_attempt = _state.last_recovery_at.get(node_id)
        cooldown = max(int(settings.health_self_heal_cooldown_seconds), 0)
        if last_attempt is not None and now - last_attempt < cooldown:
            row["self_heal_state"] = "cooldown"
            return None

        attempts = _state.recovery_attempts.get(node_id, 0)
        max_attempts = max(int(settings.health_self_heal_max_attempts), 1)
        retry_window = max(int(settings.health_self_heal_retry_window_seconds), cooldown)
        if attempts >= max_attempts:
            if last_attempt is not None and now - last_attempt < retry_window:
                row["self_heal_state"] = "retry_window"
                return None
            attempts = 0
            _state.recovery_attempts[node_id] = 0

        attempt = attempts + 1
        reason = str(row.get("reason") or health)
        recovery = self._recover_node(node, reason=reason, attempt=attempt)
        _state.recovery_attempts[node_id] = attempt
        _state.last_recovery_at[node_id] = now
        _state.failure_streaks[node_id] = 0

        event = {
            "node_id": node_id,
            "attempt": attempt,
            "reason": reason,
            **recovery,
        }
        row["self_heal"] = event

        # Xray restart can be verified immediately. Provider reboot is asynchronous
        # and remains out of the pool until a later healthcheck reports healthy.
        if recovery.get("health_status") == "healthy":
            node.health_status = "healthy"
            node.status = "active"
            row["health_status"] = "healthy"
            self._clear_node_state(node_id)
            self._audit(node_id, "node_self_heal_recovered", event)
        else:
            node.health_status = health
            self._audit(node_id, "node_self_heal_action", event)

        logger.warning(
            "self-heal node=%s attempt=%s action=%s status=%s reason=%s",
            node_id,
            attempt,
            recovery.get("action"),
            recovery.get("status"),
            reason,
        )
        return event

    def _recover_node(self, node: Any, *, reason: str, attempt: int) -> dict:
        ssh_restart = self._restart_xray_via_ssh(node)
        if ssh_restart is True:
            recheck_delay = max(float(settings.health_self_heal_recheck_seconds), 0.0)
            if recheck_delay:
                time.sleep(recheck_delay)
            rechecked = self._recheck_node(node)
            if rechecked.get("health_status") == "healthy":
                return {
                    "status": "ok",
                    "action": "restart_xray",
                    "health_status": "healthy",
                    "detail": rechecked.get("reason", "xray_restarted"),
                }

        provider_reboot = self._reboot_via_provider(node, attempt=attempt)
        if provider_reboot:
            return provider_reboot

        script_recovery = self._run_recovery_script(node, reason=reason, attempt=attempt)
        if script_recovery:
            return script_recovery

        return {
            "status": "failed",
            "action": "none",
            "health_status": "down",
            "detail": "no_available_recovery_transport",
        }

    def _restart_xray_via_ssh(self, node: Any) -> bool | None:
        endpoint = str(getattr(node, "endpoint", "") or "").strip()
        private_key_data = str(getattr(node, "ssh_private_key", "") or "").strip()
        if not endpoint or not private_key_data:
            return None

        try:
            import paramiko
        except ImportError:
            return None

        pkey = self._load_private_key(paramiko, private_key_data)
        if pkey is None:
            return None

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            timeout = max(int(settings.firstvds_ssh_connect_timeout_seconds), 5)
            client.connect(
                hostname=endpoint,
                username=settings.firstvds_ssh_user,
                pkey=pkey,
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            _, stdout, _ = client.exec_command(
                "systemctl restart xray && systemctl is-active xray",
                timeout=15,
            )
            output = stdout.read().decode(errors="ignore").strip()
            return output == "active"
        except Exception:
            logger.info("self-heal SSH restart unavailable node=%s", node.id, exc_info=True)
            return None
        finally:
            client.close()

    @staticmethod
    def _load_private_key(paramiko: Any, private_key_data: str) -> Any | None:
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

    def _recheck_node(self, node: Any) -> dict:
        checker = getattr(self.provisioning, "_check_single_node", None)
        if not callable(checker):
            return {"health_status": "unknown", "reason": "recheck_not_supported"}
        try:
            return checker(node)
        except Exception:
            logger.warning("self-heal recheck failed node=%s", node.id, exc_info=True)
            return {"health_status": "unknown", "reason": "recheck_error"}

    def _reboot_via_provider(self, node: Any, *, attempt: int) -> dict | None:
        vps_id = str(getattr(node, "firstvds_vps_id", "") or "").strip()
        client = getattr(self.provisioning, "client", None)
        reboot = getattr(client, "reboot_vds", None)
        if not vps_id or not callable(reboot):
            return None

        hard_after = max(int(settings.health_self_heal_hard_reboot_after_attempt), 1)
        hard = attempt >= hard_after
        try:
            reboot(vps_id, hard=hard)
            return {
                "status": "pending",
                "action": "hard_reboot_vps" if hard else "reboot_vps",
                "health_status": "down",
                "detail": "waiting_for_provider_reboot",
            }
        except Exception as exc:
            logger.warning("provider reboot failed node=%s", node.id, exc_info=True)
            return {
                "status": "failed",
                "action": "provider_reboot_failed",
                "health_status": "down",
                "detail": f"{type(exc).__name__}:{exc}",
            }

    @staticmethod
    def _run_recovery_script(node: Any, *, reason: str, attempt: int) -> dict | None:
        script = str(settings.node_self_heal_script or "").strip()
        if not script:
            return None
        payload = f"{node.id}|{getattr(node, 'endpoint', '')}|{reason}|{attempt}"
        try:
            result = subprocess.run(
                [script, payload],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "status": "failed",
                "action": "recovery_script",
                "health_status": "down",
                "detail": f"{type(exc).__name__}:{exc}",
            }
        return {
            "status": "pending" if result.returncode == 0 else "failed",
            "action": "recovery_script",
            "health_status": "down",
            "detail": (result.stderr or result.stdout or "").strip()[:500],
        }

    def _audit(self, node_id: int, action: str, details: dict) -> None:
        self.audit.write(
            "system",
            "health_self_heal",
            action,
            "vpn_node",
            str(node_id),
            details,
        )

    @staticmethod
    def _clear_node_state(node_id: int) -> None:
        _state.failure_streaks.pop(node_id, None)
        _state.recovery_attempts.pop(node_id, None)
        _state.last_recovery_at.pop(node_id, None)
