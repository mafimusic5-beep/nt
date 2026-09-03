from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from src.backend.repositories.audit_repo import AuditRepository
from src.backend.repositories.node_repo import NodeRepository
from src.common.config import settings
from src.common.models import VpnNode

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RenewalMutationResult:
    ok: bool
    detail: str


class RenewalTransport(Protocol):
    def disable_auto_renew(self, node: VpnNode) -> RenewalMutationResult: ...


class ScriptRenewalTransport:
    """Provider hook that can only disable renewal; deletion is never exposed."""

    def disable_auto_renew(self, node: VpnNode) -> RenewalMutationResult:
        script = (settings.node_renewal_script or "").strip()
        if not script:
            return RenewalMutationResult(False, "renewal_adapter_not_configured")
        payload = {
            "action": "disable_auto_renew",
            "idempotency_key": f"emery-do-not-renew-{node.id}-{node.contract_id}",
            "node_id": node.id,
            "provider": node.provider,
            "provider_server_id": node.provider_server_id or node.firstvds_vps_id,
            "contract_id": node.contract_id,
            "destructive_actions_allowed": False,
        }
        try:
            result = subprocess.run(
                [script, json.dumps(payload, ensure_ascii=False)],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return RenewalMutationResult(False, f"renewal_script_failed:{type(exc).__name__}")
        try:
            response = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            response = {}
        ok = (
            result.returncode == 0
            and isinstance(response, dict)
            and response.get("ok") is True
            and response.get("auto_renew") is False
        )
        return RenewalMutationResult(
            ok,
            str(
                response.get("detail")
                if isinstance(response, dict)
                else result.stderr or f"exit_{result.returncode}"
            )[:500],
        )


class RenewalPlannerService:
    def __init__(
        self,
        db: Session,
        transport: RenewalTransport | None = None,
    ) -> None:
        self.db = db
        self.repo = NodeRepository(db)
        self.audit = AuditRepository(db)
        self.transport = transport or ScriptRenewalTransport()

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _retirement_key(cls, node: VpnNode) -> tuple:
        paid_until = cls._as_utc(node.paid_until) or datetime.max.replace(tzinfo=timezone.utc)
        return (
            0 if node.health_status == "down" else 1 if node.health_status == "degraded" else 2,
            -max(int(node.renewal_price_eur_cents or 0), 0),
            paid_until,
            node.id,
        )

    def preview(self, *, now: datetime | None = None) -> dict:
        current_time = now or datetime.now(timezone.utc)
        horizon = current_time + timedelta(days=max(int(settings.renewal_planning_horizon_days), 1))
        grouped: dict[str, list[VpnNode]] = {}
        for node in self.repo.list_nodes(None):
            grouped.setdefault(node.region_code or "unknown", []).append(node)

        regions: list[dict] = []
        actions: list[dict] = []
        for region_code, nodes in sorted(grouped.items()):
            client_count = sum(max(int(node.current_clients or 0), 0) for node in nodes)
            renewable = [node for node in nodes if node.renewal_status != "do_not_renew" and node.auto_renew]
            retained_capacity = sum(max(int(node.capacity_clients or 0), 0) for node in renewable)
            min_single_capacity = max((int(node.capacity_clients or 0) for node in renewable), default=0)
            required_capacity = client_count + max(int(settings.pool_family_headroom_devices), 0)
            if nodes:
                required_capacity = max(required_capacity, min_single_capacity)

            candidates = sorted(
                (node for node in renewable if int(node.current_clients or 0) == 0 and node.provider != "manual_vps"),
                key=self._retirement_key,
            )
            recommended: list[dict] = []
            retained_count = len(renewable)
            for node in candidates:
                node_capacity = max(int(node.capacity_clients or 0), 0)
                if retained_count <= 1 or retained_capacity - node_capacity < required_capacity:
                    continue
                retained_capacity -= node_capacity
                retained_count -= 1
                paid_until = self._as_utc(node.paid_until)
                due_within_horizon = paid_until is not None and paid_until <= horizon
                applicable = bool(
                    due_within_horizon
                    and node.contract_id
                    and node.renewal_status != "do_not_renew"
                )
                row = {
                    "node_id": node.id,
                    "name": node.name,
                    "provider": node.provider,
                    "contract_id": node.contract_id,
                    "paid_until": paid_until.isoformat() if paid_until else None,
                    "renewal_price_eur_cents": node.renewal_price_eur_cents,
                    "reason": "surplus_zero_client_capacity",
                    "due_within_horizon": due_within_horizon,
                    "applicable": applicable,
                }
                recommended.append(row)
                if applicable:
                    actions.append({"region_code": region_code, **row})

            regions.append(
                {
                    "region_code": region_code,
                    "current_clients": client_count,
                    "required_capacity": required_capacity,
                    "retained_capacity": retained_capacity,
                    "retained_nodes": retained_count,
                    "recommended_do_not_renew": recommended,
                }
            )
        return {
            "generated_at": current_time.isoformat(),
            "horizon_days": max(int(settings.renewal_planning_horizon_days), 1),
            "regions": regions,
            "actions": actions,
        }

    def apply(self, *, now: datetime | None = None) -> dict:
        plan = self.preview(now=now)
        results: list[dict] = []
        for action in plan["actions"]:
            node = self.repo.get_node(int(action["node_id"]))
            if node is None:
                results.append({**action, "status": "failed", "detail": "node_not_found"})
                continue
            if not settings.auto_renewal_actions_enabled:
                results.append(
                    {**action, "status": "blocked", "detail": "auto_renewal_actions_disabled"}
                )
                continue
            result = self.transport.disable_auto_renew(node)
            if not result.ok:
                node.renewal_status = "action_failed"
                node.do_not_renew_reason = result.detail[:255]
                results.append({**action, "status": "failed", "detail": result.detail})
                continue
            node.auto_renew = False
            node.renewal_status = "do_not_renew"
            node.do_not_renew_reason = str(action["reason"])[:255]
            self.audit.write(
                "system",
                "renewal_planner",
                "node_auto_renew_disabled",
                "vpn_node",
                str(node.id),
                {
                    "contract_id": node.contract_id,
                    "reason": node.do_not_renew_reason,
                    "server_deleted": False,
                },
            )
            results.append({**action, "status": "applied", "detail": result.detail})
        self.db.commit()
        return {**plan, "results": results}
