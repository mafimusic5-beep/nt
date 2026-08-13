from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from sqlalchemy.orm import Session

from src.backend.repositories.node_repo import NodeRepository
from src.common.config import settings


@dataclass(slots=True)
class RegionCapacity:
    region_code: str
    total_capacity: int
    current_clients: int
    online_nodes: int
    total_nodes: int
    fill_percent: int
    free_slots: int
    headroom_slots: int
    pending_nodes: int
    recovering_nodes: int
    can_accept_family: bool
    needs_provisioning: bool
    status: str
    recommendation: str


class CapacityService:
    def __init__(self, db: Session):
        self.db = db
        self.node_repo = NodeRepository(db)

    def list_regions(self) -> list[RegionCapacity]:
        grouped: dict[str, list] = defaultdict(list)
        for node in self.node_repo.list_nodes(None):
            if node.region_code:
                grouped[node.region_code].append(node)

        rows: list[RegionCapacity] = []
        for region_code, nodes in grouped.items():
            online_nodes = [n for n in nodes if n.status == "active" and n.health_status in {"healthy", "degraded"}]
            pending_nodes = [n for n in nodes if n.status in {"draft", "provisioning", "provision_failed"}]
            recovering_nodes = [
                n
                for n in nodes
                if n.status == "active"
                and (
                    getattr(n, "health_status", "unknown") == "down"
                    or int(getattr(n, "consecutive_health_failures", 0) or 0) > 0
                    or getattr(n, "recovery_status", "idle") != "idle"
                )
            ]
            total_capacity = sum(max(n.capacity_clients, 0) for n in online_nodes)
            current_clients = sum(max(n.current_clients, 0) for n in online_nodes)
            free_slots = max(total_capacity - current_clients, 0)
            fill_percent = int(round((current_clients / total_capacity) * 100)) if total_capacity else 0
            status, recommendation = self._status_for(total_capacity, free_slots)
            needs_provisioning = (
                status in {"missing", "scale_required", "urgent"}
                and not pending_nodes
                # A temporarily unavailable node is repaired in place. Do not
                # turn an outage into an accidental order for a replacement.
                and not recovering_nodes
            )
            rows.append(
                RegionCapacity(
                    region_code=region_code,
                    total_capacity=total_capacity,
                    current_clients=current_clients,
                    online_nodes=len(online_nodes),
                    total_nodes=len(nodes),
                    fill_percent=fill_percent,
                    free_slots=free_slots,
                    headroom_slots=settings.pool_family_headroom_devices,
                    pending_nodes=len(pending_nodes),
                    recovering_nodes=len(recovering_nodes),
                    can_accept_family=free_slots >= settings.pool_family_headroom_devices,
                    needs_provisioning=needs_provisioning,
                    status=status,
                    recommendation=recommendation,
                )
            )

        return sorted(
            rows,
            key=lambda r: (
                self._status_rank(r.status),
                -r.fill_percent,
                r.online_nodes,
                r.region_code,
            ),
        )

    def worst_region(self) -> RegionCapacity | None:
        regions = self.list_regions()
        if not regions:
            return None
        return sorted(
            regions,
            key=lambda r: (
                self._status_rank(r.status),
                -r.fill_percent,
                r.online_nodes,
                r.free_slots,
                r.region_code,
            ),
        )[0]

    def region_requiring_scale(self) -> RegionCapacity | None:
        return next((row for row in self.list_regions() if row.needs_provisioning), None)

    def alert_text(self) -> str:
        regions = self.list_regions()
        if not regions:
            return (
                "🚨 Серверов пока нет.\n\n"
                "Нужно купить первый VPS вручную.\n"
                "Минимум: 1 vCPU / 2 GB RAM / Debian 12.\n\n"
                "После покупки отправь:\n"
                "/add_config region=nl name=\"Netherlands 1\" endpoint=<IP> config=<vless://...>"
            )

        worst = self.worst_region()
        assert worst is not None
        lines = ["📊 Ёмкость регионов", ""]
        for row in regions:
            icon = {
                "ok": "✅",
                "warning": "⚠️",
                "stop_new_users": "🟠",
                "urgent": "🚨",
                "scale_required": "🟠",
                "missing": "🚨",
            }.get(row.status, "ℹ️")
            lines.append(
                f"{icon} {row.region_code}: {row.fill_percent}% "
                f"({row.current_clients}/{row.total_capacity}, свободно {row.free_slots}, узлов online {row.online_nodes})"
            )
        lines.extend(["", "Главный приоритет:", self._buy_recommendation(worst)])
        return "\n".join(lines)

    @staticmethod
    def _status_for(total_capacity: int, free_slots: int) -> tuple[str, str]:
        if total_capacity <= 0:
            return "missing", "buy_first_server_for_region"
        if free_slots <= 0:
            return "urgent", "buy_one_more_server_now"
        if free_slots < settings.pool_family_headroom_devices:
            return "scale_required", "buy_one_more_server_now"
        if free_slots < settings.pool_family_headroom_devices * 2:
            return "warning", "prepare_one_more_server"
        return "ok", "no_action"

    @staticmethod
    def _status_rank(status: str) -> int:
        return {
            "missing": 0,
            "urgent": 1,
            "scale_required": 2,
            "warning": 3,
            "stop_new_users": 4,
            "ok": 5,
        }.get(status, 5)

    @staticmethod
    def _buy_recommendation(row: RegionCapacity) -> str:
        if row.recovering_nodes:
            return (
                f"🛠 {row.region_code}: восстанавливается узлов {row.recovering_nodes}. "
                "Покупка замены заблокирована до результата recovery-agent."
            )
        if row.status == "ok":
            return f"✅ {row.region_code}: пока докупать не нужно."
        return (
            f"🚨 Регион: {row.region_code}\n"
            f"Заполненность: {row.fill_percent}%\n"
            f"Пользователи: {row.current_clients}/{row.total_capacity}\n"
            f"Свободно: {row.free_slots}\n\n"
            "Купить вручную:\n"
            "Provider: RackNerd или другой дешёвый годовой VPS\n"
            "Plan: 1 vCPU / 2 GB RAM минимум\n"
            "OS: Debian 12\n\n"
            "После покупки добавь готовый VLESS-конфиг:\n"
            f"/add_config region={row.region_code} name=\"{row.region_code.upper()} 1\" endpoint=<IP> config=<vless://...>"
        )
