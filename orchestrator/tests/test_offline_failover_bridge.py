"""A dead server must not pin a user forever, or bypass central gate access."""
from __future__ import annotations

import pytest
import sqlite3

import pool_reservation_bridge as bridge
import storage
from device_auth import DeviceAuthError
from test_device_gate_auth import registered_assignment, _authorize


def test_central_gate_rejects_old_assignment_before_detach(registered_assignment):
    device = registered_assignment
    arguments, result = _authorize(device, device["private_key"])
    assert result["allowed"] is True
    assert not storage.quarantine_device_pool_assignment(device["code"], device["device_id"], 999)
    assert storage.quarantine_device_pool_assignment(
        device["code"], device["device_id"], 17,
    )
    assert storage.quarantine_device_pool_assignment(
        device["code"], device["device_id"], 17,
    )  # retry does not unlock the gate

    with pytest.raises(DeviceAuthError, match="device_gate_not_authorized"):
        _authorize(device, device["private_key"])
    def current_assignment_id():
        with sqlite3.connect(device["database_path"]) as con:
            return con.execute(
                "SELECT pool_assignment_id FROM code_devices WHERE device_id = ?",
                (device["device_id"],),
            ).fetchone()[0]

    assert current_assignment_id() == 17

    # Stale cleanup must never erase a newly allocated pool credential.
    storage.clear_device_pool_assignment(
        device["code"], device["device_id"], expected_assignment_id=18,
    )
    assert current_assignment_id() == 17
    storage.clear_device_pool_assignment(
        device["code"], device["device_id"], expected_assignment_id=17,
    )
    assert current_assignment_id() is None


def setup_bridge(monkeypatch, *, quarantine_raises=False, normal_release=False, preflight_down=True):
    events = []
    entitlement = {
        "code": "ABC-123",
        "plan": "personal",
        "expires_at": "2028-01-01T00:00:00Z",
    }
    saved = {"pool_node_id": 4, "pool_assignment_id": 50, "pool_status": "active"}
    monkeypatch.setattr(storage, "get_device_pool_entitlement", lambda c, d: entitlement)
    monkeypatch.setattr(storage, "get_device_pool_assignment", lambda c, d: saved)
    monkeypatch.setattr(bridge, "_subject_key", lambda c, d: "a" * 64)

    def release(old_id):
        events.append(("remote-release", old_id))
        if not normal_release:
            raise bridge.PoolBridgeError("assignment_release_failed", 503)
        return {"status": "revoked"}

    def deny_gate(code, device, assignment):
        events.append(("central-deny", assignment))
        return True

    def quarantine(assignment, subject_key, destination):
        events.append(("remote-quarantine", assignment, destination))
        if quarantine_raises:
            raise bridge.PoolBridgeError("destination_node_unavailable", 409)
        return {"status": "quarantined"}

    def clear(code, device, expected_assignment_id=None):
        events.append(("clear-old", expected_assignment_id))

    def prepare(**kwargs):
        events.append(("prepare-new", kwargs["node_id"]))
        return {"pool_assignment_id": 51, "pool_status": "active"}

    def probe(assignment, subject, destination):
        events.append(("probe", assignment, destination))
        return preflight_down

    monkeypatch.setattr(bridge, "probe_offline_assignment", probe)
    monkeypatch.setattr(bridge, "release_assignment", release)
    monkeypatch.setattr(storage, "quarantine_device_pool_assignment", deny_gate)
    monkeypatch.setattr(bridge, "quarantine_offline_assignment", quarantine)
    monkeypatch.setattr(storage, "clear_device_pool_assignment", clear)
    monkeypatch.setattr(bridge, "prepare_assignment", prepare)
    monkeypatch.setattr(storage, "save_device_pool_assignment", lambda c, d, a: None)
    monkeypatch.setattr(bridge, "confirm_persisted_assignment", lambda a: a)
    return events


def test_offline_failover_denies_gate_before_quarantine_and_new_prepare(monkeypatch):
    events = setup_bridge(monkeypatch)
    result = bridge.refresh_stored_assignment("ABC-123", "device-id", node_id=5)
    assert result["pool_assignment_id"] == 51
    assert events == [
        ("probe", 50, 5),
        ("central-deny", 50),
        ("remote-quarantine", 50, 5),
        ("clear-old", 50),
        ("prepare-new", 5),
    ]


def test_failed_quarantine_never_prepares_new_assignment(monkeypatch):
    events = setup_bridge(monkeypatch, quarantine_raises=True)
    with pytest.raises(bridge.PoolBridgeError, match="destination_node_unavailable"):
        bridge.refresh_stored_assignment("ABC-123", "device-id", node_id=5)
    assert [entry[0] for entry in events] == [
        "probe", "central-deny", "remote-quarantine",
    ]


def test_normal_switch_does_not_quarantine(monkeypatch):
    events = setup_bridge(monkeypatch, normal_release=True, preflight_down=False)
    result = bridge.refresh_stored_assignment("ABC-123", "device-id", node_id=5)
    assert result["pool_assignment_id"] == 51
    assert [entry[0] for entry in events] == [
        "probe", "remote-release", "clear-old", "prepare-new",
    ]


def test_remote_release_errors_other_than_timeout_fail_closed(monkeypatch):
    events = setup_bridge(monkeypatch, preflight_down=False)
    def non_offline_failure(assignment):
        events.append(("remote-release", assignment))
        raise bridge.PoolBridgeError("assignment_maintenance_in_progress", 409)
    monkeypatch.setattr(bridge, "release_assignment", non_offline_failure)
    with pytest.raises(bridge.PoolBridgeError, match="assignment_maintenance_in_progress"):
        bridge.refresh_stored_assignment("ABC-123", "device-id", node_id=5)
    assert [entry[0] for entry in events] == ["probe", "remote-release"]


def test_probe_fallback_when_health_flips_during_release(monkeypatch):
    events = setup_bridge(monkeypatch, preflight_down=False)
    bridge.refresh_stored_assignment("ABC-123", "device-id", node_id=5)
    assert [entry[0] for entry in events] == [
        "probe", "remote-release", "central-deny",
        "remote-quarantine", "clear-old", "prepare-new",
    ]
