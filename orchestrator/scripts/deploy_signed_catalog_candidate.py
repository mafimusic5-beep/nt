#!/usr/bin/env python3
"""Deploy a pre-tested, production-preserving Skryon API candidate.

The existing API code and SQLite database must already have been backed up.
If service startup or public health/signed-catalog checks fail, roll back.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LIVE = Path("/opt/nt/orchestrator")
STAGED = Path("/tmp/skryon_catalog_candidate")
BACKUP = Path("/root/skryon_security_20261026")
SERVICE = "skryon-api.service"
FILES = ("api.py", "device_auth.py", "client_version_policy.py")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def install_atomically(source: Path, target: Path, owner_reference: Path) -> None:
    info = owner_reference.stat()
    # Use atomic replacement within the same directory, without exposing
    # partially written modules to the running Python interpreter.
    fd, temporary_name = tempfile.mkstemp(prefix=".skryon-tmp-", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, out)
            out.flush()
            os.fsync(out.fileno())
        os.chown(temporary, info.st_uid, info.st_gid)
        os.chmod(temporary, stat.S_IMODE(info.st_mode))
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def service_restart() -> None:
    subprocess.run(["systemctl", "restart", SERVICE], timeout=25, check=True)
    for _ in range(8):
        outcome = subprocess.run(
            ["systemctl", "is-active", "--quiet", SERVICE],
            timeout=8,
        )
        if outcome.returncode == 0:
            return
        time.sleep(0.8)
    raise RuntimeError("Skryon API service did not become active")


def request_status(path: str) -> tuple[int, bytes]:
    request = Request(
        "https://skryon.ru" + path,
        headers={"Cache-Control": "no-cache", "User-Agent": "Skryon-security-rollout/1"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            return response.status, response.read(32768)
    except HTTPError as error:
        return error.code, error.read(32768)


def smoke() -> None:
    errors = []
    for _ in range(5):
        try:
            status, payload = request_status("/health")
            require(status == 200, f"health: expected 200, got {status}")
            require(json.loads(payload).get("ok") is True, "health body invalid")
            status, payload = request_status("/api/vpn/servers/authorized")
            require(status == 426, f"catalog: expected 426, got {status}")
            decoded = json.loads(payload)
            require(decoded.get("reason") == "client_proof_upgrade_required",
                    "catalog did not fail closed")
            status, payload = request_status("/api/vpn/servers")
            require(status == 200 and isinstance(json.loads(payload), list),
                    "legacy Android server metadata is unavailable")
            print("SMOKE_OK: /health=200, signed-catalog=426, Android legacy-list=200")
            return
        except (RuntimeError, ValueError, OSError) as error:
            errors.append(type(error).__name__ + ":" + str(error))
            time.sleep(1.5)
    raise RuntimeError("Post-deployment verification failed: " + "; ".join(errors[-2:]))


def main() -> None:
    require(os.geteuid() == 0, "Must be executed by root on the API host")
    for f in ("api.py", "device_auth.py"):
        live = LIVE / f
        saved = BACKUP / f
        require(live.is_file() and saved.is_file(), f"Missing live or backup {f}")
        require(not live.is_symlink(), f"Unsafe symlink at {live}")
        require(live.read_bytes() == saved.read_bytes(),
                f"Production {f} changed after the backup; aborting")
    require((BACKUP / "skryon.db").is_file() and
            (BACKUP / "skryon.db").stat().st_size > 100000,
            "An independently backed-up Skryon database is required")
    for f in FILES:
        source = STAGED / f
        require(source.is_file(), f"Missing tested candidate {source}")
        compile(source.read_text(encoding="utf-8"), str(source), "exec")
    policy_existed = (LIVE / "client_version_policy.py").exists()
    require(not policy_existed, "Policy module already deployed; refuse duplicate rollout")
    restart_started = False
    try:
        for f in FILES:
            install_atomically(STAGED / f, LIVE / f, LIVE / "api.py")
        restart_started = True
        service_restart()
        smoke()
    except Exception as error:
        print(f"DEPLOY_FAILED: {type(error).__name__}: {error}")
        if restart_started or any((LIVE / f).exists() for f in FILES):
            try:
                for f in ("api.py", "device_auth.py"):
                    install_atomically(BACKUP / f, LIVE / f, LIVE / f)
                if not policy_existed:
                    (LIVE / "client_version_policy.py").unlink(missing_ok=True)
                service_restart()
                status, _ = request_status("/health")
                print(f"ROLLBACK_DONE: API status={status}")
            except Exception as rollback_error:
                print(f"ROLLBACK_PROBLEM: {type(rollback_error).__name__}: {rollback_error}")
        raise
    print("DEPLOY_OK: signed-catalog endpoint is online.")
    print("LEGACY_ACCESS_STILL_ALLOWED: Android/old Windows rollout not finished.")


if __name__ == "__main__":
    main()
