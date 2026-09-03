#!/usr/bin/env python3
"""Pinned-SSH preflight for an explicitly selected, already purchased VPS.

The inspect action is read-only. Claim records exclusive installation ownership
and hardens SSH, but never purchases, deletes or replaces a server.
"""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path

STATE = Path("/var/lib/emery-manual-vps")
SSH_CONFIG = Path("/etc/ssh/sshd_config.d/00-emery-manual-vps.conf")
SSH_CONTENT = (
    "PasswordAuthentication no\nKbdInteractiveAuthentication no\n"
    "PermitRootLogin prohibit-password\nAllowTcpForwarding no\nX11Forwarding no\n"
)


class PreflightError(RuntimeError):
    pass


def run(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True)
        if len(result.stdout) > 65536:
            raise PreflightError("manual_vps_preflight_output_limit")
        return result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError("manual_vps_preflight_command_failed") from exc


def validate(payload: dict) -> None:
    if set(payload) != {"action", "operation_id", "node_id", "profile_sha256", "management_ipv4"}:
        raise PreflightError("manual_vps_preflight_payload_invalid")
    if (payload["action"] not in {"inspect", "claim"}
            or str(uuid.UUID(payload["operation_id"])) != payload["operation_id"]
            or type(payload["node_id"]) is not int or not 0 < payload["node_id"] < 2147483647
            or not re.fullmatch(r"[a-f0-9]{64}", payload["profile_sha256"])):
        raise PreflightError("manual_vps_preflight_payload_invalid")
    address = ipaddress.ip_address(payload["management_ipv4"])
    if address.version != 4 or not address.is_global:
        raise PreflightError("manual_vps_preflight_management_ip_invalid")


def secure_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise PreflightError("manual_vps_owner_directory_unsafe")


def owner(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "action"}


def is_owned(payload: dict) -> bool:
    if not STATE.exists() and not STATE.is_symlink():
        return False
    secure_directory(STATE)
    path = STATE / "owner.json"
    if not path.exists():
        # The directory can precede an interrupted, not-yet-committed claim.
        if list(STATE.iterdir()):
            raise PreflightError("manual_vps_owner_marker_missing")
        return False
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 4096:
        raise PreflightError("manual_vps_owner_marker_unsafe")
    if json.loads(path.read_text()) != owner(payload):
        raise PreflightError("manual_vps_other_installation_owns_host")
    return True


def check_environment(payload: dict) -> bool:
    if os.geteuid() != 0:
        raise PreflightError("manual_vps_remote_root_required")
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    if release.get("ID") != "debian" or release.get("VERSION_ID") not in {"12", "13"}:
        raise PreflightError("manual_vps_debian_12_or_13_required")
    if platform.machine() != "x86_64":
        raise PreflightError("manual_vps_amd64_required")
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if len(connection) != 4 or connection[0] != payload["management_ipv4"] or connection[3] != "22":
        raise PreflightError("manual_vps_management_ip_or_ssh_port_mismatch")
    owned = is_owned(payload)
    # Do not bootstrap on a production web/app/VPN host even when its service is
    # stopped. Only a fresh VPS is supported; there is no force/overwrite option.
    for name in (
        "/opt/skryon", "/etc/xray", "/etc/nginx", "/etc/apache2",
        "/etc/wireguard", "/etc/openvpn", "/var/lib/docker",
    ):
        path = Path(name)
        if path.exists() or path.is_symlink():
            raise PreflightError("manual_vps_existing_software_requires_review")
    for name in ("/opt", "/srv"):
        children = list(Path(name).iterdir()) if Path(name).exists() else []
        if any(not (owned and name == "/opt" and child.name == "emery") for child in children):
            raise PreflightError("manual_vps_nonempty_application_directory")
    if owned:
        return True
    for name in ("/opt/emery", "/usr/local/etc/xray", "/var/lib/emery-ionos",
                 "/var/lib/emery-regional-policy", "/usr/local/bin/xray"):
        path = Path(name)
        if path.exists() or path.is_symlink():
            raise PreflightError("manual_vps_existing_software_requires_review")
    for args, permitted in (
        (["ss", "-H", "-ltn"], {22, 53}),
        (["ss", "-H", "-lun"], {53, 68, 123, 546}),
    ):
        for line in run(args).splitlines():
            fields = line.split()
            try:
                port = int(fields[3].rsplit(":", 1)[1])
            except (IndexError, ValueError) as exc:
                raise PreflightError("manual_vps_listener_inspection_failed") from exc
            if port not in permitted:
                raise PreflightError("manual_vps_existing_listeners_require_review")
    return False


def harden_ssh() -> None:
    SSH_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if SSH_CONFIG.exists() or SSH_CONFIG.is_symlink():
        info = SSH_CONFIG.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or SSH_CONFIG.read_text() != SSH_CONTENT):
            raise PreflightError("manual_vps_ssh_hardening_conflict")
    else:
        with SSH_CONFIG.open("x") as stream:
            created = True
            os.chmod(SSH_CONFIG, 0o600)
            stream.write(SSH_CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
    try:
        run(["/usr/sbin/sshd", "-t"])
        effective = run(["/usr/sbin/sshd", "-T"])
        if not all(value in effective.splitlines() for value in (
            "passwordauthentication no", "kbdinteractiveauthentication no",
            "allowtcpforwarding no", "x11forwarding no",
        )):
            raise PreflightError("manual_vps_ssh_hardening_not_effective")
        run(["systemctl", "reload", "ssh"])
    except Exception:
        # Only undo this newly created snippet, never the operator's SSH config.
        if created:
            SSH_CONFIG.unlink()
        raise


def execute(payload: dict) -> dict:
    validate(payload)
    owned = check_environment(payload)
    if payload["action"] == "claim":
        if not owned:
            STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
            secure_directory(STATE)
            # O_EXCL prevents two controllers from claiming the same host.
            with (STATE / "owner.json").open("x") as stream:
                os.chmod(STATE / "owner.json", 0o600)
                json.dump(owner(payload), stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        harden_ssh()
    return {"ok": True, "owned": owned or payload["action"] == "claim"}


def main() -> None:
    os.umask(0o077)
    try:
        raw = sys.stdin.read(8193)
        if len(raw) > 8192:
            raise PreflightError("manual_vps_preflight_input_limit")
        result = execute(json.loads(raw))
    except Exception as exc:
        detail = str(exc) if isinstance(exc, PreflightError) else "manual_vps_remote_preflight_failed"
        if not re.fullmatch(r"manual_vps_[a-z0-9_]{1,100}", detail):
            detail = "manual_vps_remote_preflight_failed"
        result = {"ok": False, "detail": detail}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
