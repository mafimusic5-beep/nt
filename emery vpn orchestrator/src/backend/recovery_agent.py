from __future__ import annotations

import argparse
import logging
import signal
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import select

from src.backend.core.logging import setup_logging
from src.backend.services.node_recovery_service import NodeRecoveryService
from src.common.config import settings
from src.common.db import SessionLocal
from src.common.models import VpnNode

logger = logging.getLogger(__name__)


class RecoveryAgent:
    """Long-running Kubernetes worker for every active public VPN server."""

    def __init__(self) -> None:
        self.stop_event = threading.Event()

    def _active_node_ids(self) -> list[int]:
        db = SessionLocal()
        try:
            return list(
                db.scalars(
                    select(VpnNode.id)
                    .where(VpnNode.status == "active")
                    .order_by(VpnNode.region_code.asc(), VpnNode.id.asc())
                ).all()
            )
        finally:
            db.close()

    @staticmethod
    def _run_node(node_id: int) -> dict:
        db = SessionLocal()
        try:
            return NodeRecoveryService(db).run_node(node_id)
        finally:
            db.close()

    def run_cycle(self) -> dict:
        node_ids = self._active_node_ids()
        if not node_ids:
            return {"checked": 0, "results": []}
        max_workers = min(
            len(node_ids),
            max(int(settings.recovery_max_parallel_nodes), 1),
        )
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="node-recovery") as executor:
            futures = {executor.submit(self._run_node, node_id): node_id for node_id in node_ids}
            for future in as_completed(futures):
                node_id = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    logger.exception("recovery worker crashed for node=%s", node_id)
                    results.append(
                        {
                            "node_id": node_id,
                            "status": "worker_failed",
                            "detail": f"{type(exc).__name__}:{exc}",
                        }
                    )
        results.sort(key=lambda row: int(row.get("node_id", 0)))
        return {"checked": len(node_ids), "results": results}

    def _schedule_nodes(
        self,
        executor: ThreadPoolExecutor,
        in_flight: dict[int, Future],
    ) -> dict:
        """Collect completed jobs and keep one independent job per active node."""
        completed: list[dict] = []
        for node_id, future in list(in_flight.items()):
            if not future.done():
                continue
            in_flight.pop(node_id, None)
            try:
                completed.append(future.result())
            except Exception as exc:  # noqa: BLE001
                logger.exception("recovery worker crashed for node=%s", node_id)
                completed.append(
                    {
                        "node_id": node_id,
                        "status": "worker_failed",
                        "detail": f"{type(exc).__name__}:{exc}",
                    }
                )

        node_ids = self._active_node_ids()
        active_ids = set(node_ids)
        for node_id, future in list(in_flight.items()):
            if node_id not in active_ids and future.cancel():
                in_flight.pop(node_id, None)

        scheduled = 0
        for node_id in node_ids:
            if node_id in in_flight:
                continue
            in_flight[node_id] = executor.submit(self._run_node, node_id)
            scheduled += 1

        return {
            "active": len(node_ids),
            "scheduled": scheduled,
            "in_flight": len(in_flight),
            "completed": completed,
        }

    @staticmethod
    def _touch_heartbeat() -> None:
        path = Path(settings.recovery_heartbeat_file)
        try:
            path.touch(exist_ok=True)
        except OSError as exc:
            logger.warning("cannot update recovery-agent heartbeat: %s", exc)

    def run_forever(self) -> None:
        interval = max(int(settings.recovery_probe_interval_seconds), 5)
        max_workers = max(int(settings.recovery_max_parallel_nodes), 1)
        logger.info("recovery-agent started: interval=%ss", interval)
        executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="node-recovery",
        )
        in_flight: dict[int, Future] = {}
        try:
            while not self.stop_event.is_set():
                try:
                    result = self._schedule_nodes(executor, in_flight)
                    # Liveness represents a functioning scheduler/DB loop, not
                    # an unrelated timer thread that could remain alive after
                    # all recovery scheduling had stopped.
                    self._touch_heartbeat()
                    logger.info(
                        "recovery schedule: active=%s scheduled=%s in_flight=%s completed=%s",
                        result["active"],
                        result["scheduled"],
                        result["in_flight"],
                        len(result["completed"]),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("recovery schedule failed")
                self.stop_event.wait(interval)
        finally:
            for future in in_flight.values():
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Emery public-node recovery agent")
    parser.add_argument("--once", action="store_true", help="run one probe cycle and exit")
    args = parser.parse_args()

    setup_logging(settings.log_level)
    agent = RecoveryAgent()
    signal.signal(signal.SIGTERM, lambda *_: agent.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: agent.stop_event.set())
    if args.once:
        logger.info("recovery cycle result: %s", agent.run_cycle())
        return
    agent.run_forever()


if __name__ == "__main__":
    main()
