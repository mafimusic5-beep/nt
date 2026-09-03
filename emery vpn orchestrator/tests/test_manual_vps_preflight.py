"""Offline remote-helper tests: every filesystem path maps into tmp_path."""
from __future__ import annotations

import importlib.util
import json
import os
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "manual_preflight_tests",
    Path(__file__).resolve().parents[1] / "deploy/manual-vps/preflight_node.py",
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


@pytest.fixture
def host(tmp_path, monkeypatch):
    def path(value):
        return tmp_path / str(value).lstrip("/")

    release = path("/etc/os-release")
    release.parent.mkdir(parents=True)
    release.write_text('ID=debian\nVERSION_ID="12"\n')
    monkeypatch.setattr(helper, "Path", path)
    monkeypatch.setattr(helper, "STATE", path("/var/lib/emery-manual-vps"))
    monkeypatch.setattr(helper, "SSH_CONFIG", path("/etc/ssh/sshd_config.d/00-emery-manual-vps.conf"))
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("SSH_CONNECTION", "9.9.9.9 51234 93.184.216.34 22")
    original_lstat = Path.lstat

    def root_owned_lstat(self, *args, **kwargs):
        value = original_lstat(self, *args, **kwargs)
        if self.is_relative_to(tmp_path):
            fields = list(value)
            fields[4] = 0  # Simulate root ownership even on non-root CI workers.
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(Path, "lstat", root_owned_lstat)

    def command(args):
        if args == ["ss", "-H", "-ltn"]:
            return "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 128 [::]:22 [::]:*\n"
        if args == ["ss", "-H", "-lun"]:
            return "UNCONN 0 0 0.0.0.0:68 0.0.0.0:*\n"
        if args == ["/usr/sbin/sshd", "-T"]:
            return "passwordauthentication no\nkbdinteractiveauthentication no\nallowtcpforwarding no\nx11forwarding no\n"
        if args in (["/usr/sbin/sshd", "-t"], ["systemctl", "reload", "ssh"]):
            return ""
        pytest.fail("Unexpected remote command: " + repr(args))

    runner = Mock(side_effect=command)
    monkeypatch.setattr(helper, "run", runner)
    payload = {
        "action": "inspect", "operation_id": str(uuid.uuid4()), "node_id": 42,
        "profile_sha256": "a" * 64, "management_ipv4": "9.9.9.9",
    }
    return path, payload, runner


def test_inspect_never_writes_files_or_changes_ssh(host):
    path, payload, runner = host
    assert helper.execute(payload) == {"ok": True, "owned": False}
    assert not path("/var/lib/emery-manual-vps").exists()
    assert not helper.SSH_CONFIG.exists()
    assert all(call.args[0][0] == "ss" for call in runner.call_args_list)


@pytest.mark.parametrize("name", [
    "/opt/emery", "/etc/nginx", "/etc/apache2", "/etc/wireguard",
    "/usr/local/etc/xray", "/var/lib/docker", "/srv/production-app", "/opt/production-bot",
])
def test_existing_application_is_not_overwritten(host, name):
    path, payload, _ = host
    existing = path(name)
    existing.mkdir(parents=True)
    marker = existing / "user-data"
    marker.write_text("preserve")
    payload["action"] = "claim"
    with pytest.raises(helper.PreflightError):
        helper.execute(payload)
    assert marker.read_text() == "preserve"
    assert not helper.SSH_CONFIG.exists()
    assert not helper.STATE.exists()


@pytest.mark.parametrize("connection", [
    "8.8.8.8 12345 93.184.216.34 22",
    "9.9.9.9 12345 93.184.216.34 2222",
    "",
])
def test_wrong_management_ip_or_ssh_port_cannot_lock_operator_out(host, monkeypatch, connection):
    _, payload, runner = host
    monkeypatch.setenv("SSH_CONNECTION", connection)
    with pytest.raises(helper.PreflightError, match="management_ip_or_ssh_port"):
        helper.execute(dict(payload, action="claim"))
    runner.assert_not_called()
    assert not helper.STATE.exists()


def test_existing_listener_blocks_installation(host):
    _, payload, runner = host
    runner.side_effect = lambda args: "LISTEN 0 128 0.0.0.0:8080 0.0.0.0:*\n"
    with pytest.raises(helper.PreflightError, match="existing_listeners"):
        helper.execute(dict(payload, action="claim"))
    assert not helper.SSH_CONFIG.exists()


def test_wrong_os_or_architecture_is_rejected(host, monkeypatch):
    path, payload, runner = host
    path("/etc/os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    with pytest.raises(helper.PreflightError, match="debian_12_or_13"):
        helper.execute(payload)
    path("/etc/os-release").write_text('ID=debian\nVERSION_ID="13"\n')
    monkeypatch.setattr(helper.platform, "machine", lambda: "aarch64")
    with pytest.raises(helper.PreflightError, match="amd64"):
        helper.execute(payload)
    runner.assert_not_called()


def test_claim_preserves_host_identity_and_resumes_only_its_own_operation(host):
    path, payload, runner = host
    payload["action"] = "claim"
    assert helper.execute(payload)["owned"]
    marker = (helper.STATE / "owner.json").read_text()
    assert json.loads(marker) == helper.owner(payload)
    assert helper.SSH_CONFIG.read_text() == helper.SSH_CONTENT
    assert not (helper.SSH_CONFIG.stat().st_mode & 0o077)
    # Now the installer may have created its own files/listeners.
    path("/opt/emery").mkdir(parents=True)
    assert helper.execute(dict(payload, action="inspect"))["owned"]
    assert helper.execute(payload)["owned"]
    assert (helper.STATE / "owner.json").read_text() == marker
    with pytest.raises(helper.PreflightError, match="other_installation"):
        helper.execute(dict(payload, operation_id=str(uuid.uuid4())))
    with pytest.raises(helper.PreflightError, match="other_installation"):
        helper.execute(dict(payload, profile_sha256="b" * 64))
    # No host keys or authorized_keys are generated, deleted, or replaced.
    assert not path("/etc/ssh/ssh_host_ed25519_key").exists()
    assert not path("/root/.ssh/authorized_keys").exists()


def test_world_readable_ownership_marker_is_refused(host):
    _, payload, _ = host
    helper.execute(dict(payload, action="claim"))
    (helper.STATE / "owner.json").chmod(0o644)
    with pytest.raises(helper.PreflightError, match="owner_marker_unsafe"):
        helper.execute(payload)


def test_resume_stops_if_an_unrelated_app_was_added_after_claim(host):
    path, payload, _ = host
    helper.execute(dict(payload, action="claim"))
    path("/etc/nginx").mkdir(parents=True)
    with pytest.raises(helper.PreflightError, match="existing_software"):
        helper.execute(dict(payload, action="claim"))


def test_symlinked_owner_directory_is_refused(host):
    path, payload, _ = host
    target = path("/other-data")
    target.mkdir(parents=True)
    helper.STATE.parent.mkdir(parents=True)
    helper.STATE.symlink_to(target)
    with pytest.raises(helper.PreflightError, match="owner_directory_unsafe"):
        helper.execute(dict(payload, action="claim"))
    assert list(target.iterdir()) == []


def test_conflicting_ssh_snippet_is_preserved(host):
    _, payload, _ = host
    helper.SSH_CONFIG.parent.mkdir(parents=True)
    helper.SSH_CONFIG.write_text("# existing operator configuration")
    with pytest.raises(helper.PreflightError, match="ssh_hardening_conflict"):
        helper.execute(dict(payload, action="claim"))
    assert helper.SSH_CONFIG.read_text() == "# existing operator configuration"


def test_invalid_ssh_configuration_does_not_reload_or_leave_new_snippet(host):
    _, payload, runner = host
    original = runner.side_effect

    def fail_validation(args):
        if args == ["/usr/sbin/sshd", "-t"]:
            raise helper.PreflightError("manual_vps_preflight_command_failed")
        return original(args)

    runner.side_effect = fail_validation
    with pytest.raises(helper.PreflightError):
        helper.execute(dict(payload, action="claim"))
    assert not helper.SSH_CONFIG.exists()
    assert ["systemctl", "reload", "ssh"] not in [call.args[0] for call in runner.call_args_list]


def test_payload_injection_is_rejected_before_commands(host):
    _, payload, runner = host
    with pytest.raises((helper.PreflightError, ValueError)):
        helper.execute(dict(payload, operation_id="bad;whoami"))
    with pytest.raises(helper.PreflightError):
        helper.execute(dict(payload, command="arbitrary command"))
    runner.assert_not_called()
