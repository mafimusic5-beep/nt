#!/usr/bin/env python3
"""Apply only a reviewed, three-hunk policy-service patch on the manager VPS.

No DB schema or row changes. Exact zero-fuzz patching, compile, backup and
automatic rollback if the backend service health check fails.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import tempfile
import time
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path("/opt/nt")
TARGET = "emery vpn orchestrator/src/backend/services/traffic_policy_service.py"
BASE_SHA = "db1b824e483c42389c4b3b81944551c2d43d59dd"
FIX_SHA = "f7f829bc01227f51f0d3d5a2aa6e676eaac0dbfe"
REPO = "mafimusic5-beep/nt"


def read_git_version(ref: str) -> str:
    url = f"https://raw.githubusercontent.com/{REPO}/{ref}/" + quote(TARGET, safe="/")
    with urlopen(url, timeout=20) as result:
        raw = result.read(200_001)
    if len(raw) > 200_000:
        raise RuntimeError("source_too_large")
    return raw.decode("utf-8")


def prepare(stage: Path) -> tuple[Path, Path]:
    live = ROOT / TARGET
    if not live.is_file():
        raise RuntimeError("live_service_source_missing")
    base = read_git_version(BASE_SHA)
    head = read_git_version(FIX_SHA)
    if 'routing["rules"] = [api_dispatch_rule] + managed_rules + preserved_rules' not in head:
        raise RuntimeError("patched_source_guard_failed")
    patch = "".join(difflib.unified_diff(
        base.splitlines(keepends=True),
        head.splitlines(keepends=True),
        fromfile=live.name, tofile=live.name, n=3,
    ))
    dest = stage / live.name
    shutil.copy2(live, dest)
    patch_path = stage / "routing-fix.patch"
    patch_path.write_text(patch, encoding="utf-8")
    result = subprocess.run(
        ["patch", "-s", "-p0", "--batch", "--fuzz=0", "-i", str(patch_path)],
        cwd=stage, capture_output=True, text=True, timeout=20,
    )
    if result.returncode:
        raise RuntimeError("LIVE_SOURCE_DIVERGED_REFUSE_DEPLOY:" + (
            result.stderr + result.stdout
        )[-400:])
    py_compile.compile(str(dest), cfile=str(stage / "compiled.pyc"), doraise=True)
    return live, dest


def health() -> None:
    for retry in range(14):
        try:
            with urlopen("http://127.0.0.1:9330/api/v1/health", timeout=4) as res:
                if res.status == 200:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("modern_api_healthcheck_failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("root_required")
    with tempfile.TemporaryDirectory(prefix="skryon-policy-stage-") as tmp:
        live, staged = prepare(Path(tmp))
        print("PATCH_MATCHES_LIVE_ZERO_FUZZ=True", flush=True)
        if args.check:
            print("CHECK_ONLY_NO_CHANGES=True", flush=True)
            return
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = Path("/opt/nt-deploy-backups") / ("xray-api-route-" + stamp)
        backup_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        backup_file = backup_dir / live.name
        shutil.copy2(live, backup_file)
        os.chmod(backup_file, 0o600)
        print("BACKUP_CODE_OK", str(backup_file), flush=True)
        try:
            temp_live = live.with_name(".traffic-policy-patched.tmp")
            shutil.copy2(staged, temp_live)
            os.chmod(temp_live, live.stat().st_mode & 0o777)
            os.replace(temp_live, live)
            subprocess.run(
                ["systemctl", "restart", "emery-backend.service"],
                check=True, timeout=40,
            )
            health()
            print("BACKEND_POLICY_FIX_DEPLOYED_AND_HEALTHY=True", flush=True)
        except Exception:
            print("DEPLOY_FAILED_ROLLING_BACK_POLICY_SERVICE", flush=True)
            shutil.copy2(backup_file, live)
            subprocess.run(
                ["systemctl", "restart", "emery-backend.service"],
                timeout=40, check=False,
            )
            health()
            raise


if __name__ == "__main__":
    main()
