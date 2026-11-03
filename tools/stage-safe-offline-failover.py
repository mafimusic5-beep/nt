#!/usr/bin/env python3
"""Patch only reviewed failover hunks onto the LIVE /opt/nt tree.

Usage:
  python3 /tmp/skryon-failover-stage.py --check
  python3 /tmp/skryon-failover-stage.py --apply

NEVER resets customer data; creates SQLite online backups before code changes.
Never overwrites unknown neighboring live code: patch --fuzz=0 must match.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import os
from pathlib import Path
import py_compile
import shutil
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path("/opt/nt")
BASE = "db1b824e483c42389c4b3b81944551c2d43d59dd"
HEAD = "a2e7c54220a31ac313cff9894f9d23f560f5c30f"
REPO = "mafimusic5-beep/nt"
FILES = [
    "orchestrator/api.py",
    "orchestrator/storage.py",
    "orchestrator/pool_reservation_bridge.py",
    "emery vpn orchestrator/src/backend/schemas/pool_bridge.py",
    "emery vpn orchestrator/src/backend/api/routes.py",
    "emery vpn orchestrator/src/backend/services/pool_assignment_service.py",
    "emery vpn orchestrator/src/backend/services/node_orchestration_service.py",
]
SERVICES = ["emery-backend.service", "skryon-api.service"]


def source_at(ref: str, filename: str) -> str:
    url = (
        f"https://raw.githubusercontent.com/{REPO}/{ref}/"
        + quote(filename, safe="/")
    )
    with urlopen(url, timeout=18) as response:
        body = response.read()
    if len(body) > 2_000_000:
        raise RuntimeError("Source size limit exceeded")
    return body.decode("utf-8")


def stage_patches(stage: Path) -> list[tuple[Path, Path]]:
    result = []
    for rel in FILES:
        live = ROOT / rel
        if not live.is_file():
            raise RuntimeError(f"Live source file missing: {rel}")
        base = source_at(BASE, rel)
        head = source_at(HEAD, rel)
        patch = "".join(
            difflib.unified_diff(
                base.splitlines(keepends=True),
                head.splitlines(keepends=True),
                fromfile=live.name,
                tofile=live.name,
                n=3,
            )
        )
        if not patch:
            raise RuntimeError(f"Expected code change missing: {rel}")
        staged = stage / rel
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live, staged)
        patch_file = staged.parent / (live.name + ".failover.patch")
        patch_file.write_text(patch, encoding="utf-8")
        process = subprocess.run(
            ["patch", "-s", "-p0", "--batch", "--fuzz=0", "-i", str(patch_file)],
            cwd=staged.parent,
            text=True, capture_output=True, timeout=15,
        )
        if process.returncode:
            raise RuntimeError(
                f"REFUSE to overwrite diverged live code: {rel}; "
                f"{process.stderr[:300]} {process.stdout[:300]}"
            )
        py_compile.compile(
            str(staged), cfile=str(stage / f"compile-{len(result)}.pyc"),
            doraise=True,
        )
        result.append((live, staged))
    return result


def backup_databases(folder: Path) -> None:
    databases = (
        ("legacy.db", ROOT / "orchestrator/skryon.db"),
        ("modern.db", ROOT / "emery vpn orchestrator/data/app.db"),
    )
    for output_name, live_path in databases:
        if not live_path.is_file():
            raise RuntimeError(f"Database missing: {live_path}")
        with sqlite3.connect(f"file:{quote(str(live_path))}?mode=ro", uri=True) as source:
            with sqlite3.connect(str(folder / output_name)) as target:
                source.backup(target)
        os.chmod(folder / output_name, 0o600)
        print("SQLITE_BACKUP_OK", output_name, flush=True)


def service(command: str) -> None:
    subprocess.run(["systemctl", command, *SERVICES], check=True, timeout=45)


def verify() -> None:
    for url in (
        "http://127.0.0.1:9330/api/v1/health",
        "http://127.0.0.1:8080/health",
    ):
        for attempt in range(14):
            try:
                with urlopen(url, timeout=4) as result:
                    if result.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError(f"Post-restart health failed: {url}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.check == args.apply:
        parser.error("choose exactly one of --check or --apply")
    if os.geteuid() != 0:
        raise RuntimeError("Root required")
    if not ROOT.is_dir():
        raise RuntimeError("Live tree missing")
    with tempfile.TemporaryDirectory(prefix="skryon-failover-staging-") as tmp:
        files = stage_patches(Path(tmp))
        print("ALL_PATCH_HUNKS_MATCH_LIVE=True", len(files), flush=True)
        if args.check:
            print("CHECK_ONLY_NO_CHANGES=True", flush=True)
            return

        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = Path("/opt/nt-deploy-backups") / f"safe-offline-failover-{stamp}"
        backup.mkdir(mode=0o700, parents=True, exist_ok=False)
        for live, _ in files:
            target = backup / live.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live, target)
        backup_databases(backup)
        print("CODE_AND_DB_BACKUPS_CREATED", str(backup), flush=True)

        wrote = []
        try:
            for live, staged in files:
                sibling = live.with_name(live.name + ".failover-new")
                shutil.copy2(staged, sibling)
                os.chmod(sibling, live.stat().st_mode & 0o777)
                os.replace(sibling, live)
                wrote.append(live)
            print("ATOMIC_CODE_PATCH_APPLIED", len(wrote), flush=True)
            service("restart")
            verify()
            print("BOTH_PRODUCTION_SERVICES_HEALTHY=True", flush=True)
            print("FAILOVER_DEPLOY_COMPLETED=True", flush=True)
        except Exception:
            print("DEPLOY_FAILED_RESTORING_CODE", flush=True)
            for live in wrote:
                original = backup / live.relative_to(ROOT)
                shutil.copy2(original, live)
            try:
                service("restart")
                verify()
                print("ROLLBACK_SERVICE_HEALTH_OK", flush=True)
            finally:
                raise


if __name__ == "__main__":
    main()
