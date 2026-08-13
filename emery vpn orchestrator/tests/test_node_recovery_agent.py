from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from datetime import datetime, timezone

from src.backend.recovery_agent import RecoveryAgent
from src.backend.services.node_recovery_service import (
    NodeRecoveryService,
    ProbeResult,
    RecoveryActionResult,
    SshAndProviderRecoveryTransport,
    VlessTcpProbe,
)
from src.common.config import settings
from src.common.models import VpnNode


class SequenceProbe:
    def __init__(self, results: list[bool], *, fallback: bool = False):
        self.results = list(results)
        self.fallback = fallback
        self.calls: list[int] = []

    def probe(self, node: VpnNode) -> ProbeResult:
        self.calls.append(node.id)
        ok = self.results.pop(0) if self.results else self.fallback
        return ProbeResult(ok, "test_probe", "203.0.113.10", 443)


class TrackingProbe:
    def __init__(self):
        self.calls: list[int] = []

    def probe(self, node: VpnNode) -> ProbeResult:
        self.calls.append(node.id)
        return ProbeResult(True, "test_probe", node.endpoint, 443)


class FakeRecoveryTransport:
    def __init__(self, *, restart_ok: bool = True, reboot_ok: bool = True):
        self.restart_ok = restart_ok
        self.reboot_ok = reboot_ok
        self.calls: list[tuple[str, int]] = []

    def restart_xray(self, node: VpnNode) -> RecoveryActionResult:
        self.calls.append(("restart_xray", node.id))
        return RecoveryActionResult(self.restart_ok, "test_restart")

    def reboot_server(self, node: VpnNode) -> RecoveryActionResult:
        self.calls.append(("reboot_server", node.id))
        return RecoveryActionResult(self.reboot_ok, "test_reboot")


def _add_node(
    db_session,
    node_id: int,
    *,
    region: str = "de",
    status: str = "active",
) -> VpnNode:
    node = VpnNode(
        id=node_id,
        region_code=region,
        name=f"{region}-{node_id}",
        provider="manual",
        status=status,
        endpoint=f"203.0.113.{node_id}",
        config_payload=(
            f"vless://00000000-0000-0000-0000-{node_id:012d}"
            f"@203.0.113.{node_id}:443?security=reality#{region}"
        ),
        health_status="healthy",
    )
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)
    return node


def _service(db_session, probe, transport=None) -> NodeRecoveryService:
    return NodeRecoveryService(
        db_session,
        probe=probe,
        transport=transport or FakeRecoveryTransport(),
        sleeper=lambda _seconds: None,
    )


def test_vless_probe_uses_listener_from_import_link():
    node = VpnNode(
        region_code="de",
        name="de-1",
        endpoint="wrong.example:8443",
        config_payload=(
            "vless://11111111-1111-4111-8111-111111111111@203.0.113.10:443"
            "?type=tcp&security=reality#Germany"
        ),
    )

    assert VlessTcpProbe.endpoint(node) == ("203.0.113.10", 443)


def test_manual_node_uses_shared_recovery_key_when_node_key_is_missing(
    tmp_path,
    monkeypatch,
):
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("test-private-key", encoding="utf-8")
    monkeypatch.setattr(settings, "recovery_ssh_private_key_path", str(key_path))
    node = VpnNode(region_code="de", name="manual", ssh_private_key="")

    assert SshAndProviderRecoveryTransport._private_key_data(node) == "test-private-key"

    node.ssh_private_key = "node-specific-key"
    assert SshAndProviderRecoveryTransport._private_key_data(node) == "node-specific-key"


def test_provider_fallback_targets_same_existing_server(monkeypatch):
    node = VpnNode(
        id=7,
        region_code="de",
        name="ionos-de-7",
        provider="ionos_vps_plus",
        endpoint="203.0.113.7",
        provider_server_id="ionos-existing-42",
    )
    recovery = SshAndProviderRecoveryTransport()
    captured = {}
    monkeypatch.setattr(settings, "recovery_provider_reboot_script", "/opt/reboot-existing")

    def ssh_unavailable(_node):
        raise RuntimeError("ssh_unavailable")

    def fake_run(args, **_kwargs):
        captured["args"] = args
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "action": "reboot_existing_server",
                    "provider_server_id": "ionos-existing-42",
                    "detail": "accepted",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(recovery, "_connect", ssh_unavailable)
    monkeypatch.setattr(
        "src.backend.services.node_recovery_service.subprocess.run",
        fake_run,
    )

    result = recovery.reboot_server(node)
    payload = json.loads(captured["args"][1])

    assert result.ok is True
    assert captured["args"][0] == "/opt/reboot-existing"
    assert payload["action"] == "reboot_existing_server"
    assert payload["node_id"] == node.id
    assert payload["provider_server_id"] == "ionos-existing-42"
    assert "purchase" not in json.dumps(payload)


def test_provider_fallback_refuses_a_node_without_existing_server_id(monkeypatch):
    node = VpnNode(
        id=8,
        region_code="de",
        name="unidentified-node",
        provider="ionos_vps_plus",
        endpoint="203.0.113.8",
        provider_server_id="",
        firstvds_vps_id="",
    )
    recovery = SshAndProviderRecoveryTransport()
    monkeypatch.setattr(settings, "recovery_provider_reboot_script", "/opt/reboot-existing")

    def ssh_unavailable(_node):
        raise RuntimeError("ssh_unavailable")

    monkeypatch.setattr(recovery, "_connect", ssh_unavailable)

    result = recovery.reboot_server(node)

    assert result.ok is False
    assert "provider_server_id_missing" in result.detail


def test_every_active_public_node_is_checked_and_maintenance_is_ignored(db_session):
    active_de = _add_node(db_session, 1, region="de")
    active_nl = _add_node(db_session, 2, region="nl")
    _add_node(db_session, 3, region="us", status="maintenance")
    probe = TrackingProbe()

    result = _service(db_session, probe).run_once()

    assert result["checked"] == 2
    assert probe.calls == [active_de.id, active_nl.id]


def test_third_failed_probe_restarts_xray_on_same_node(db_session, monkeypatch):
    node = _add_node(db_session, 1)
    monkeypatch.setattr(settings, "recovery_failure_threshold", 3)
    monkeypatch.setattr(settings, "recovery_restart_grace_seconds", 0)
    probe = SequenceProbe([False, False, False, True])
    transport = FakeRecoveryTransport(restart_ok=True)
    service = _service(db_session, probe, transport)

    assert service.run_node(node.id)["status"] == "probe_failed"
    assert service.run_node(node.id)["status"] == "probe_failed"
    result = service.run_node(node.id)

    assert result == {"node_id": node.id, "status": "recovered", "action": "restart_xray"}
    assert transport.calls == [("restart_xray", node.id)]
    db_session.refresh(node)
    assert node.health_status == "healthy"
    assert node.consecutive_health_failures == 0
    assert node.recovery_status == "idle"


def test_failed_xray_restart_reboots_same_existing_server(db_session, monkeypatch):
    node = _add_node(db_session, 1)
    monkeypatch.setattr(settings, "recovery_failure_threshold", 1)
    monkeypatch.setattr(settings, "recovery_reboot_grace_seconds", 0)
    monkeypatch.setattr(settings, "recovery_reboot_probe_attempts", 1)
    probe = SequenceProbe([False, False, True])
    transport = FakeRecoveryTransport(restart_ok=False, reboot_ok=True)

    result = _service(db_session, probe, transport).run_node(node.id)

    assert result == {"node_id": node.id, "status": "recovered", "action": "reboot_server"}
    assert transport.calls == [
        ("restart_xray", node.id),
        ("reboot_server", node.id),
    ]


def test_cooldown_blocks_duplicate_repair_but_not_probe(db_session, monkeypatch):
    node = _add_node(db_session, 1)
    monkeypatch.setattr(settings, "recovery_failure_threshold", 1)
    monkeypatch.setattr(settings, "recovery_reboot_probe_attempts", 1)
    monkeypatch.setattr(settings, "recovery_cooldown_seconds", 300)
    probe = SequenceProbe([False, False, False], fallback=False)
    transport = FakeRecoveryTransport(restart_ok=False, reboot_ok=False)
    service = _service(db_session, probe, transport)

    assert service.run_node(node.id)["status"] == "recovery_failed"
    calls_after_repair = len(probe.calls)
    locked = service.run_node(node.id)

    assert locked["status"] == "recovery_locked"
    assert len(probe.calls) == calls_after_repair + 1
    assert transport.calls == [
        ("restart_xray", node.id),
        ("reboot_server", node.id),
    ]


def test_recovery_lease_is_acquired_atomically(db_session, monkeypatch):
    node = _add_node(db_session, 1)
    monkeypatch.setattr(settings, "recovery_lock_seconds", 600)
    service = _service(db_session, SequenceProbe([]))
    now = datetime.now(timezone.utc)

    assert service._acquire_lock(node.id, now) is True
    assert service._acquire_lock(node.id, now) is False


def test_kubernetes_agent_checks_public_nodes_in_parallel(monkeypatch):
    monkeypatch.setattr(settings, "recovery_max_parallel_nodes", 3)
    agent = RecoveryAgent()
    barrier = threading.Barrier(3, timeout=2)

    monkeypatch.setattr(agent, "_active_node_ids", lambda: [1, 2, 3])

    def worker(node_id: int) -> dict:
        barrier.wait()
        return {"node_id": node_id, "status": "healthy"}

    monkeypatch.setattr(agent, "_run_node", worker)

    result = agent.run_cycle()

    assert result["checked"] == 3
    assert [row["node_id"] for row in result["results"]] == [1, 2, 3]


def test_slow_recovery_does_not_delay_next_probe_for_other_nodes(monkeypatch):
    monkeypatch.setattr(settings, "recovery_max_parallel_nodes", 2)
    agent = RecoveryAgent()
    blocked_recovery = threading.Event()
    first_fast_probe = threading.Event()
    second_fast_probe = threading.Event()
    fast_calls = 0

    monkeypatch.setattr(agent, "_active_node_ids", lambda: [1, 2])

    def worker(node_id: int) -> dict:
        nonlocal fast_calls
        if node_id == 1:
            blocked_recovery.wait(timeout=2)
        else:
            fast_calls += 1
            (first_fast_probe if fast_calls == 1 else second_fast_probe).set()
        return {"node_id": node_id, "status": "healthy"}

    monkeypatch.setattr(agent, "_run_node", worker)
    in_flight = {}
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        agent._schedule_nodes(executor, in_flight)
        assert first_fast_probe.wait(timeout=1)
        in_flight[2].result(timeout=1)

        agent._schedule_nodes(executor, in_flight)
        assert second_fast_probe.wait(timeout=1)
        assert fast_calls == 2
        assert 1 in in_flight
    finally:
        blocked_recovery.set()
        executor.shutdown(wait=True, cancel_futures=True)
