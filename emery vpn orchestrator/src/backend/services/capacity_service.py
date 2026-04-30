from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from sqlalchemy.orm import Session

from src.backend.repositories.node_repo import NodeRepository


@dataclass(slots=True)
class RegionCapacity:
    region_code: str
    total_capacity: int
    current_clients: int
    online_nodes: int
    total_nodes: int
    fill_percent: int
    free_slots: int
    status: str
    recommendation: str


class CapacityService:
    WARNING_FILL = 70
    STOP_NEW_USERS_FILL = 80
    URGENT_FILL = 85

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
            total_capacity = sum(max(n.capacity_clients, 0) for n in online_nodes)
            current_clients = sum(max(n.current_clients, 0) for n in online_nodes)
            free_slots = max(total_capacity - current_clients, 0)
            fill_percent = int(round((current_clients / total_capacity) * 100)) if total_capacity else 0
            status, recommendation = self._status_for(total_capacity, fill_percent)
            rows.append(
                RegionCapacity(
                    region_code=region_code,
                    total_capacity=total_capacity,
                    current_clients=current_clients,
                    online_nodes=len(online_nodes),
                    total_nodes=len(nodes),
                    fill_percent=fill_percent,
                    free_slots=free_slots,
                    status=status,
                    recommendation=recommendation,
                )
            )

        return sorted(rows, key=lambda r: (self._status_rank(r.status), -r.fill_percent, r.region_code))

    def worst_region(self) -> RegionCapacity | None:
        regions = self.list_regions()
        if not regions:
            return None
        return sorted(regions, key=lambda r: (self._status_rank(r.status), -r.fill_percent, r.free_slots))[0]

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
                "missing": "🚨",
            }.get(row.status, "ℹ️")
            lines.append(
                f"{icon} {row.region_code}: {row.fill_percent}% "
                f"({row.current_clients}/{row.total_capacity}, свободно {row.free_slots}, узлов online {row.online_nodes})"
            )
        lines.extend(["", "Главный приоритет:", self._buy_recommendation(worst)])
        return "\n".join(lines)

    @classmethod
    def _status_for(cls, total_capacity: int, fill_percent: int) -> tuple[str, str]:
        if total_capacity <= 0:
            return "missing", "buy_first_server_for_region"
        if fill_percent >= cls.URGENT_FILL:
            return "urgent", "buy_one_more_server_now"
        if fill_percent >= cls.STOP_NEW_USERS_FILL:
            return "stop_new_users", "stop_assigning_new_users_and_prepare_server"
        if fill_percent >= cls.WARNING_FILL:
            return "warning", "prepare_one_more_server"
        return "ok", "no_action"

    @staticmethod
    def _status_rank(status: str) -> int:
        return {
            "missing": 0,
            "urgent": 1,
            "stop_new_users": 2,
            "warning": 3,
            "ok": 4,
        }.get(status, 5)

    @staticmethod
    def _buy_recommendation(row: RegionCapacity) -> str:
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
