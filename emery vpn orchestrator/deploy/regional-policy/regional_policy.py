#!/usr/bin/env python3
"""Server-only regional datasets and isolated Xray configuration.

Only `update` accesses the network. Policy selection and `sync-credentials`
never download data. All credentials remain on loopback behind the device gate.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import grp
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager, nullcontext
from pathlib import Path

SOURCE = "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release"
ASSETS = ("geosite.dat", "geoip.dat")
MAX_ASSET_BYTES = 128 * 1024 * 1024
MAX_AGE_SECONDS = 48 * 60 * 60
STATE = Path("/var/lib/emery-regional-policy")
SERVICE = "emery-regional-xray.service"
GROUP = "emery-regional-xray"
LISTEN_HOST = "127.0.0.2"
MANAGED_TAG = re.compile(r"emery-device-([1-9][0-9]*)-([1-9][0-9]*)\Z")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def checked_json(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("unsafe_root_owned_config")
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise RuntimeError("config_not_an_object")
    return result


def build_config(source: dict) -> dict:
    """Does not mutate the international config, UUIDs, Vision flow or speed caps."""
    result = {key: copy.deepcopy(source[key]) for key in (
        "log", "dns", "policy", "outbounds", "routing"
    ) if key in source}
    # A separate process must not compete for the primary process's log files.
    result["log"] = {"loglevel": "warning"}
    inbounds = []
    ports = set()
    for original in source.get("inbounds", []):
        tag = str(original.get("tag", ""))
        if not tag.startswith("emery-device-"):
            continue
        if (
            not MANAGED_TAG.fullmatch(tag)
            or original.get("protocol") != "vless"
            or original.get("listen") != "127.0.0.1"
            or len(original.get("settings", {}).get("clients", [])) != 1
            or original.get("settings", {}).get("fallbacks")
        ):
            raise RuntimeError("unsafe_managed_inbound")
        port = original.get("port")
        if type(port) is not int or not 1 <= port <= 65535 or port in ports:
            raise RuntimeError("invalid_managed_port")
        ports.add(port)
        inbound = copy.deepcopy(original)
        inbound["listen"] = LISTEN_HOST
        # Server sniffing is mandatory; changing Android rules cannot disable it.
        inbound["sniffing"] = {
            "enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True,
        }
        inbounds.append(inbound)
    result["inbounds"] = inbounds
    outbounds = result.setdefault("outbounds", [])
    if not outbounds or outbounds[0].get("protocol") != "freedom":
        raise RuntimeError("regional_policy_requires_direct_server_egress")
    blocked = next((out for out in outbounds if out.get("tag") == "emery-blocked"), None)
    if blocked is None:
        outbounds.append({"tag": "emery-blocked", "protocol": "blackhole"})
    elif blocked.get("protocol") != "blackhole":
        raise RuntimeError("blocked_outbound_conflict")
    direct = outbounds[0].get("tag")
    if not direct:
        raise RuntimeError("server_egress_tag_missing")
    routing = result.setdefault("routing", {})
    # Only this isolated process changes DNS resolution strategy. The original
    # process and its international rules are untouched.
    routing["domainStrategy"] = "IPOnDemand"
    routing["rules"] = [
        {"type": "field", "domain": ["ext:geosite.dat:ru-blocked-all"], "outboundTag": "emery-blocked"},
        {"type": "field", "port": "25,465,587", "outboundTag": "emery-blocked"},
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "emery-blocked"},
        # Keep DNS reachable as in the existing policy; domain restrictions
        # above still precede this transport-only DNS exception.
        {"type": "field", "port": "53", "outboundTag": direct},
        {"type": "field", "ip": [
            "ext:geoip.dat:ru-blocked", "ext:geoip.dat:ru-blocked-community", "ext:geoip.dat:re-filter",
        ], "outboundTag": "emery-blocked"},
        {"type": "field", "network": "udp", "port": "443", "outboundTag": "emery-blocked"},
    ] + list(routing.get("rules", []))
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_assets(destination: Path) -> dict[str, str]:
    opener = urllib.request.build_opener(NoRedirect)
    hashes = {}
    for name in ASSETS:
        with opener.open(f"{SOURCE}/{name}.sha256sum", timeout=20) as response:
            raw = response.read(4097)
            if response.status != 200 or len(raw) > 4096:
                raise RuntimeError("invalid_checksum_response")
        match = re.fullmatch(rb"([a-fA-F0-9]{64})(?:[ \t]+[^\r\n]+)?[\r\n]*", raw.strip())
        if not match:
            raise RuntimeError("invalid_checksum")
        expected = match[1].decode("ascii").lower()
        received = 0
        started = time.monotonic()
        with opener.open(f"{SOURCE}/{name}", timeout=20) as response, (destination / name).open("xb") as output:
            if response.status != 200:
                raise RuntimeError("invalid_dataset_response")
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_ASSET_BYTES or time.monotonic() - started > 1800:
                    raise RuntimeError("dataset_limit_exceeded")
                output.write(chunk)
        if received < 16 * 1024 or digest(destination / name) != expected:
            raise RuntimeError("dataset_checksum_mismatch")
        hashes[name] = expected
    return hashes


@contextmanager
def locked(path: Path):
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def run(command: list[str], *, env=None) -> None:
    subprocess.run(command, check=True, capture_output=True, timeout=90, env=env)


def write_json(path: Path, value: dict, mode: int = 0o640) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".policy-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def select_snapshot(state: Path, snapshot: Path) -> None:
    temporary = state / ".active-next"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(snapshot)
    os.replace(temporary, state / "active")


def current_snapshot(state: Path) -> Path | None:
    active = state / "active"
    if not active.exists():
        return None
    current = active.resolve(strict=True)
    if current.parent != state / "snapshots" or not current.name.startswith("snapshot-"):
        raise RuntimeError("invalid_snapshot_path")
    return current


def verify_snapshot(snapshot: Path) -> dict:
    manifest = checked_json(snapshot / "manifest.json")
    age = time.time() - float(manifest["updated_at"])
    if not 0 <= age < MAX_AGE_SECONDS:
        raise RuntimeError("regional_policy_expired")
    for name in (*ASSETS, "config.json"):
        path = snapshot / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("unsafe_snapshot_file")
        if digest(path) != manifest["hashes"][name]:
            raise RuntimeError("snapshot_checksum_mismatch")
    return manifest


def stop_restricted(state: Path) -> None:
    (state / "ready.json").unlink(missing_ok=True)
    run(["systemctl", "stop", SERVICE])


def publish_ready(state: Path, snapshot: Path) -> None:
    manifest = checked_json(snapshot / "manifest.json")
    config = checked_json(snapshot / "config.json")
    ports = sorted(inbound["port"] for inbound in config["inbounds"])
    # Active service state alone does not prove all credential listeners opened.
    deadline = time.monotonic() + 25
    pending = set(ports)
    while pending:
        for port in list(pending):
            try:
                with socket.create_connection((LISTEN_HOST, port), timeout=0.25):
                    pending.remove(port)
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("regional_listeners_not_ready")
        if pending:
            time.sleep(0.1)
    write_json(state / "ready.json", {
        "schema": 1, "policy": "russia", "listen_host": LISTEN_HOST,
        "ports": ports, "updated_at": manifest["updated_at"], "snapshot": snapshot.name,
        "assignments": {str(inbound["port"]): int(MANAGED_TAG.fullmatch(inbound["tag"])[1])
                        for inbound in config["inbounds"]},
    }, mode=0o644)


def activate(state: Path, snapshot: Path, source_hash: str, binary: str) -> None:
    env = dict(os.environ, XRAY_LOCATION_ASSET=str(snapshot))
    # This also verifies that EVERY mandatory geosite/geoip group exists.
    run([binary, "run", "-test", "-config", str(snapshot / "config.json")], env=env)
    previous = current_snapshot(state)
    (state / "ready.json").unlink(missing_ok=True)
    try:
        select_snapshot(state, snapshot)
        run(["systemctl", "restart", SERVICE])
        run(["systemctl", "is-active", "--quiet", SERVICE])
        publish_ready(state, snapshot)
    except Exception:
        stop_restricted(state)
        # A stale credential snapshot must never resurrect a revoked UUID. Only
        # a dataset-only update with unchanged source config may roll back.
        if previous and checked_json(previous / "manifest.json").get("source_config_sha256") == source_hash:
            verify_snapshot(previous)
            select_snapshot(state, previous)
            run(["systemctl", "restart", SERVICE])
            run(["systemctl", "is-active", "--quiet", SERVICE])
            publish_ready(state, previous)
        raise


def retain_snapshots(state: Path) -> None:
    current = current_snapshot(state)
    candidates = sorted(
        (path for path in (state / "snapshots").iterdir()
         if path.is_dir() and not path.is_symlink() and path.name.startswith("snapshot-")),
        key=lambda path: path.stat().st_mtime, reverse=True,
    )
    keep = {current}
    keep.update([path for path in candidates if path != current][:1])
    for path in candidates:
        if path not in keep:
            shutil.rmtree(path)  # Exact generated snapshot, never a supplied directory.


def prepare(action: str, source_path: Path, state: Path, binary: str, *, credential_lock_held: bool = False) -> None:
    if os.geteuid() != 0:
        raise RuntimeError("root_required")
    state = state.resolve(strict=True)
    state_info = state.stat()
    if state_info.st_uid != 0 or state_info.st_mode & 0o022:
        raise RuntimeError("unsafe_state_directory")
    group_id = grp.getgrnam(GROUP).gr_gid
    snapshots = state / "snapshots"
    snapshots.mkdir(exist_ok=True, mode=0o755)
    # Retention must not delete another worker's still-downloading directory.
    staged = Path(tempfile.mkdtemp(prefix=".staging-", dir=snapshots))
    try:
        # Large transfers are outside the credential lock. New activations do
        # not wait for the periodic updater's downloads.
        hashes = download_assets(staged) if action == "update" else None
        refreshed = time.time()
        credential_lock = nullcontext() if credential_lock_held else locked(source_path.parent / ".emery-xray-credentials.lock")
        with credential_lock, locked(state / ".update.lock"):
            source = checked_json(source_path)
            if hashes is None:
                current = current_snapshot(state)
                if current is None:
                    (state / "ready.json").unlink(missing_ok=True)
                    return  # Installed but not initialized: RF denied, international available.
                manifest = checked_json(current / "manifest.json")
                refreshed = float(manifest["updated_at"])
                if not 0 <= time.time() - refreshed < MAX_AGE_SECONDS:
                    stop_restricted(state)
                    return
                hashes = {name: manifest["hashes"][name] for name in ASSETS}
                for name in ASSETS:
                    os.link(current / name, staged / name)
            config = build_config(source)
            write_json(staged / "config.json", config)
            source_hash = digest(source_path)
            hashes["config.json"] = digest(staged / "config.json")
            write_json(staged / "manifest.json", {
                "schema": 1, "source": SOURCE, "updated_at": refreshed,
                "source_config_sha256": source_hash, "hashes": hashes,
            })
            os.chown(staged, 0, group_id)
            os.chmod(staged, 0o750)
            for path in staged.iterdir():
                os.chown(path, 0, group_id)
                os.chmod(path, 0o640)
            published = staged.with_name(staged.name.replace(".staging-", "snapshot-", 1))
            staged.rename(published)
            staged = published
            activate(state, staged, source_hash, binary)
            retain_snapshots(state)
    except Exception:
        if action == "sync-credentials":
            # Even failures before activate() must not leave obsolete UUIDs in
            # the regional process. International Xray is managed independently.
            stop_restricted(state)
        raise
    finally:
        if staged.exists() and current_snapshot(state) != staged:
            shutil.rmtree(staged)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("update", "sync-credentials", "check-snapshot"))
    parser.add_argument("--source-config", type=Path, default=Path(os.getenv(
        "EMERY_XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json"
    )))
    parser.add_argument("--credential-lock-held", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.action == "check-snapshot":
        snapshot = current_snapshot(STATE)
        if snapshot is None:
            raise RuntimeError("regional_policy_not_initialized")
        verify_snapshot(snapshot)
    else:
        if args.credential_lock_held and args.action != "sync-credentials":
            raise RuntimeError("invalid_lock_mode")
        prepare(args.action, args.source_config, STATE, os.getenv("EMERY_XRAY_BINARY", "/usr/local/bin/xray"),
                credential_lock_held=args.credential_lock_held)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Regional policy failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
