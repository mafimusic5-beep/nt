#!/usr/bin/env python3
"""One-time, fail-closed Xray API routing repair. No DB or user credentials changed.

Usage:
  python3 repair-xray-api-route.py --check
  python3 repair-xray-api-route.py --apply

The live Xray config contains private REALITY keys, so no full config output
is ever printed. Backup stays root-only on the VPN VPS.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

CONFIG = Path("/usr/local/etc/xray/config.json")
LOCK = Path("/usr/local/etc/xray/.emery-xray-policy.lock")
BACKUPS = Path("/root/skryon-xray-config-backups")
API_ADDR = "127.0.0.1:10085"
API_RULE = {
    "type": "field",
    "ruleTag": "skryon-api-dispatch",
    "inboundTag": ["api"],
    "outboundTag": "api",
}


def repaired(original: dict) -> tuple[dict, int, int]:
    """Pure calculation, safe to test with synthetic config."""
    config = json.loads(json.dumps(original))
    api = config.get("api")
    if not isinstance(api, dict) or api.get("tag") != "api":
        raise RuntimeError("unexpected_api_tag_no_changes")
    if api.get("listen") != API_ADDR:
        raise RuntimeError("unexpected_api_listener_no_changes")
    if "RoutingService" not in (api.get("services") or []):
        raise RuntimeError("routing_service_disabled_no_changes")
    routing = config.get("routing")
    if not isinstance(routing, dict) or not isinstance(routing.get("rules"), list):
        raise RuntimeError("routing_rules_missing_no_changes")

    old = routing["rules"]
    out = [API_RULE.copy()]
    seen = {json.dumps(API_RULE, sort_keys=True, separators=(",", ":"))}
    for rule in old:
        if not isinstance(rule, dict):
            raise RuntimeError("invalid_rule_no_changes")
        tags = rule.get("inboundTag") or []
        if isinstance(tags, str):
            tags = [tags]
        # Replace any old API dispatch rule with one highest-priority copy.
        if tags == ["api"] and rule.get("outboundTag") == "api":
            continue
        fingerprint = json.dumps(rule, sort_keys=True, separators=(",", ":"))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(rule)

    routing["rules"] = out
    return config, len(old), len(out)


def xray_config_valid(path: Path) -> None:
    result = subprocess.run(
        ["xray", "run", "-test", "-config", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        raise RuntimeError("xray_config_validation_failed:" + (
            result.stderr + result.stdout
        )[-1400:])


def api_ok() -> bool:
    try:
        test = subprocess.run(
            ["xray", "api", "lsrules", "--server=" + API_ADDR],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8, check=False,
        )
        return test.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def restart_verify() -> None:
    subprocess.run(["systemctl", "restart", "xray"], timeout=35, check=True)
    for _ in range(8):
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", "xray"],
            timeout=5, check=False,
        ).returncode == 0
        if active and api_ok():
            return
        time.sleep(1)
    raise RuntimeError("xray_api_not_responding_after_restart")


def replace_safely(data: bytes, mode: int, uid: int, gid: int) -> None:
    fd, name = tempfile.mkstemp(prefix=".skryon-xray-", dir=CONFIG.parent)
    try:
        os.fchmod(fd, stat.S_IMODE(mode))
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, CONFIG)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> None:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("root_required")
    with LOCK.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        raw = CONFIG.read_bytes()
        file_stat = CONFIG.stat()
        original = json.loads(raw)
        candidate, before, after = repaired(original)
        changed = candidate != original
        print("ROUTING_RULES_BEFORE", before, flush=True)
        print("ROUTING_RULES_AFTER", after, flush=True)
        print("DUPLICATES_ELIMINATED", before + 1 - after, flush=True)
        print("API_ROUTE_FIRST", candidate["routing"]["rules"][0] == API_RULE, flush=True)
        print("CHANGE_REQUIRED", changed, flush=True)
        if args.check:
            print("CHECK_ONLY_NO_CHANGES=True", flush=True)
            return

        data = (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode()
        fd, name = tempfile.mkstemp(prefix=".skryon-xray-check-", suffix=".json", dir=CONFIG.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            xray_config_valid(Path(name))
        finally:
            if os.path.exists(name):
                os.unlink(name)
        if not changed:
            print("CONFIG_ALREADY_REPAIRED=True", flush=True)
            if not api_ok():
                raise RuntimeError("xray_api_unreachable_even_with_correct_route")
            return

        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = BACKUPS / ("config-" + stamp + ".json")
        if backup.exists():
            raise RuntimeError("backup_exists_refuse_overwrite")
        with backup.open("xb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        print("PRIVATE_CONFIG_BACKUP_OK", str(backup), flush=True)

        try:
            replace_safely(data, file_stat.st_mode, file_stat.st_uid, file_stat.st_gid)
            restart_verify()
            print("XRAY_API_GRPC_HEALTHY=True", flush=True)
            print("ROUTING_REPAIR_DEPLOYED=True", flush=True)
        except Exception:
            print("REPAIR_FAILED_RESTORING_ORIGINAL_CONFIG", flush=True)
            replace_safely(raw, file_stat.st_mode, file_stat.st_uid, file_stat.st_gid)
            subprocess.run(["systemctl", "restart", "xray"], timeout=35, check=False)
            raise


if __name__ == "__main__":
    main()
