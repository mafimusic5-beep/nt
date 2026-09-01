from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

POLICY_PATH = Path(__file__).resolve().parents[1] / "deploy/regional-policy/regional_policy.py"
SPEC = importlib.util.spec_from_file_location("emery_regional_policy", POLICY_PATH)
policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = policy
SPEC.loader.exec_module(policy)


def source_config():
    dedicated = {
        "tag": "emery-device-17-30", "protocol": "vless", "listen": "127.0.0.1", "port": 20000,
        "settings": {"clients": [{"id": "11111111-1111-4111-8111-111111111111", "flow": "xtls-rprx-vision"}]},
        "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"privateKey": "fixture"}},
    }
    return {
        "inbounds": [dict(dedicated, tag="template", listen="0.0.0.0", port=443), dedicated],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"domainStrategy": "AsIs", "rules": [
            {"type": "field", "network": "tcp,udp", "outboundTag": "direct"},
        ]},
        "api": {"tag": "api", "listen": "127.0.0.1:10085"},
        "log": {"access": "/primary-access.log", "error": "/primary-error.log"},
    }


def test_isolated_policy_preserves_original_config_credentials_and_vision():
    original = source_config()
    unchanged = copy.deepcopy(original)
    result = policy.build_config(original)
    assert original == unchanged
    assert len(result["inbounds"]) == 1
    inbound = result["inbounds"][0]
    assert inbound["listen"] == "127.0.0.2"
    assert inbound["tag"] == "emery-device-17-30"
    assert inbound["port"] == 20000
    assert inbound["settings"] == original["inbounds"][1]["settings"]
    assert inbound["streamSettings"] == original["inbounds"][1]["streamSettings"]
    assert inbound["sniffing"]["enabled"] is True
    assert "api" not in result
    assert result["log"] == {"loglevel": "warning"}
    assert original["routing"]["domainStrategy"] == "AsIs"
    assert result["routing"]["domainStrategy"] == "IPOnDemand"


def test_every_existing_restriction_precedes_catch_all_and_smtp_private_stay_blocked():
    result = policy.build_config(source_config())
    rules = result["routing"]["rules"]
    assert rules[0]["domain"] == ["ext:geosite.dat:ru-blocked-all"]
    ip_rule = next(rule for rule in rules if "ext:geoip.dat:ru-blocked" in rule.get("ip", []))
    assert ip_rule["ip"] == ["ext:geoip.dat:ru-blocked", "ext:geoip.dat:ru-blocked-community", "ext:geoip.dat:re-filter"]
    assert ip_rule["outboundTag"] == "emery-blocked"
    assert rules.index(ip_rule) < len(rules) - 1
    assert any(rule.get("ip") == ["geoip:private"] and rule["outboundTag"] == "emery-blocked" for rule in rules)
    assert any(rule.get("port") == "25,465,587" and rule["outboundTag"] == "emery-blocked" for rule in rules)
    dns = next(rule for rule in rules if rule.get("port") == "53")
    assert dns["outboundTag"] == "direct"


@pytest.mark.parametrize("changes", [
    {"listen": "0.0.0.0"}, {"protocol": "socks"}, {"port": True}, {"port": 70000},
    {"tag": "emery-device-invalid"},
    {"settings": {"clients": []}},
    {"settings": {"clients": [{}], "fallbacks": [{"dest": 80}]}},
])
def test_unsafe_managed_listener_cannot_be_published(changes):
    source = source_config()
    source["inbounds"][1].update(changes)
    with pytest.raises(RuntimeError):
        policy.build_config(source)


def test_revoked_device_is_not_mirrored_and_port_reuse_has_new_assignment():
    source = source_config()
    source["inbounds"] = source["inbounds"][:1]
    assert policy.build_config(source)["inbounds"] == []
    source = source_config()
    source["inbounds"][1]["tag"] = "emery-device-18-30"
    assert policy.build_config(source)["inbounds"][0]["tag"] == "emery-device-18-30"


class Response(io.BytesIO):
    status = 200


def fake_download(monkeypatch, corrupt=False):
    bodies = {name: (name.encode() * 2048) for name in policy.ASSETS}
    hashes = {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}
    urls = []

    def open_url(url, timeout):
        urls.append(url)
        assert url.startswith(policy.SOURCE + "/")
        assert timeout == 20
        name = url.rsplit("/", 1)[1]
        if name.endswith(".sha256sum"):
            asset = name.removesuffix(".sha256sum")
            return Response(f"{hashes[asset]}  ./publish/{asset}\n".encode())
        return Response(bodies[name] + (b"corruption" if corrupt else b""))

    monkeypatch.setattr(policy.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_url))
    return hashes, urls


def test_server_download_verifies_both_complete_assets(monkeypatch, tmp_path):
    hashes, urls = fake_download(monkeypatch)
    assert policy.download_assets(tmp_path) == hashes
    assert len(urls) == 4
    assert all(policy.digest(tmp_path / name) == hashes[name] for name in policy.ASSETS)


def test_corrupt_dataset_is_rejected(monkeypatch, tmp_path):
    fake_download(monkeypatch, corrupt=True)
    with pytest.raises(RuntimeError, match="checksum_mismatch"):
        policy.download_assets(tmp_path)


@pytest.fixture
def transaction(monkeypatch, tmp_path):
    state = tmp_path / "state"
    snapshots = state / "snapshots"
    snapshots.mkdir(parents=True)
    previous = snapshots / "snapshot-previous"
    candidate = snapshots / "snapshot-candidate"
    previous.mkdir()
    candidate.mkdir()
    for path in (previous, candidate):
        (path / "manifest.json").write_text(json.dumps({"source_config_sha256": "same"}))
        (path / "config.json").write_text("{}")
    (state / "active").symlink_to(previous)
    (state / "ready.json").write_text("previous-ready")
    commands = []
    # Transaction tests isolate systemd/network and the privileged filesystem.
    monkeypatch.setattr(policy, "checked_json", lambda path: json.loads(path.read_text()))
    monkeypatch.setattr(policy, "run", lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(policy, "verify_snapshot", lambda path: {})
    monkeypatch.setattr(policy, "publish_ready", lambda state, snapshot: (state / "ready.json").write_text(snapshot.name))
    return state, previous, candidate, commands


def test_bad_xray_config_never_replaces_previous_state(monkeypatch, transaction):
    state, previous, candidate, commands = transaction

    def failure(command, **kwargs):
        assert command[:3] == ["xray", "run", "-test"]
        raise RuntimeError("validation failed")

    monkeypatch.setattr(policy, "run", failure)
    with pytest.raises(RuntimeError):
        policy.activate(state, candidate, "same", "xray")
    assert policy.current_snapshot(state) == previous
    assert (state / "ready.json").read_text() == "previous-ready"


def test_only_restricted_service_restarts_on_dataset_update(transaction):
    state, previous, candidate, commands = transaction
    policy.activate(state, candidate, "same", "xray")
    assert policy.current_snapshot(state) == candidate
    assert (state / "ready.json").read_text() == candidate.name
    assert ["systemctl", "restart", "emery-regional-xray.service"] in commands
    assert all(command[-1] != "xray" for command in commands if command[0] == "systemctl")


@pytest.mark.parametrize("source_hash,restored", [("same", True), ("changed-credentials", False)])
def test_failed_restart_rolls_back_only_when_credentials_match(monkeypatch, transaction, source_hash, restored):
    state, previous, candidate, commands = transaction
    failed = False

    def run(command, **kwargs):
        nonlocal failed
        commands.append(command)
        if command[:2] == ["systemctl", "restart"] and not failed:
            failed = True
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(policy, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        policy.activate(state, candidate, source_hash, "xray")
    if restored:
        assert policy.current_snapshot(state) == previous
        assert (state / "ready.json").read_text() == previous.name
    else:
        assert not (state / "ready.json").exists()
    assert ["systemctl", "stop", "emery-regional-xray.service"] in commands


def test_retains_active_and_one_backup_without_deleting_other_paths(transaction):
    state, previous, candidate, _ = transaction
    unrelated = state / "snapshots" / "manual-backup"
    unrelated.mkdir()
    downloading = state / "snapshots" / ".staging-in-progress"
    downloading.mkdir()
    older = state / "snapshots" / "snapshot-old"
    older.mkdir()
    os.utime(older, (1, 1))
    policy.select_snapshot(state, candidate)
    policy.retain_snapshots(state)
    assert candidate.exists() and previous.exists() and unrelated.exists()
    assert downloading.exists()
    assert not older.exists()


def test_client_policy_has_no_downloader_or_local_list_dependency():
    repo = Path(__file__).resolve().parents[2]
    source = (repo / "app/src/main/java/com/v2ray/ang/handler/RegionalPolicyManager.kt").read_text()
    for forbidden in ("HttpURLConnection", "ensureRussiaAssetsFresh", "geosite:", "geoip:", "downloadVerifiedAssets"):
        assert forbidden not in source
    assert "EmeryDeviceGateConfig.descriptorFor(profile) != null" in source


def test_installer_never_restarts_primary_xray_or_changes_existing_gate_directory():
    script = (POLICY_PATH.parent / "install.sh").read_text()
    assert 'if [[ ! -d /etc/emery ]]; then' in script
    assert "restart xray" not in script
    assert "enable --now emery-regional-policy-update.timer" in script  # printed follow-up only


def test_initial_server_update_then_credential_sync_never_downloads_again(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    source_path = tmp_path / "source.json"
    source = source_config()
    source_path.write_text(json.dumps(source))
    commands = []
    fake_download(monkeypatch)
    # Unit-test privilege/filesystem boundaries; no real service or network is used.
    monkeypatch.setattr(policy.os, "geteuid", lambda: 0)
    monkeypatch.setattr(policy.os, "chown", lambda *args: None)
    monkeypatch.setattr(policy.grp, "getgrnam", lambda *args: SimpleNamespace(gr_gid=os.getgid()))
    monkeypatch.setattr(policy, "checked_json", lambda path: json.loads(path.read_text()))
    monkeypatch.setattr(policy, "run", lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(policy.socket, "create_connection", lambda *args, **kwargs: io.BytesIO())
    # Preserve all other checks but supply a trusted state-dir owner in unprivileged CI.
    original_stat = Path.stat

    def state_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == state:
            return SimpleNamespace(st_uid=0, st_mode=result.st_mode)
        return result

    monkeypatch.setattr(Path, "stat", state_stat)
    policy.prepare("update", source_path, state, "xray")
    first = policy.current_snapshot(state)
    ready = json.loads((state / "ready.json").read_text())
    assert ready["assignments"] == {"20000": 17}
    timestamp = ready["updated_at"]

    def forbidden(*args):
        pytest.fail("credential changes and policy selection must not download lists")

    monkeypatch.setattr(policy, "download_assets", forbidden)
    source["inbounds"][1]["tag"] = "emery-device-18-30"
    source_path.write_text(json.dumps(source))
    policy.prepare("sync-credentials", source_path, state, "xray")
    second = policy.current_snapshot(state)
    ready = json.loads((state / "ready.json").read_text())
    assert ready["assignments"] == {"20000": 18}
    assert ready["updated_at"] == timestamp  # Synchronizing does not fake data freshness.
    assert (first / "geosite.dat").stat().st_ino == (second / "geosite.dat").stat().st_ino

    source["inbounds"] = source["inbounds"][:1]
    source_path.write_text(json.dumps(source))
    policy.prepare("sync-credentials", source_path, state, "xray")
    assert json.loads((state / "ready.json").read_text())["assignments"] == {}
    assert len(list((state / "snapshots").iterdir())) == 2
