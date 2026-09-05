from __future__ import annotations

import json
import logging
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.backend.services.node_recovery_service import SshAndProviderRecoveryTransport
from src.common.config import settings
from src.common.models import VpnAssignment, VpnNode

logger = logging.getLogger(__name__)


class TrafficPolicyService:
    INTERNATIONAL = "international"
    RUSSIA = "russia"
    VALID = {INTERNATIONAL, RUSSIA}

    # Process-local cache only. The current app request is always authoritative.
    _cache_lock = Lock()
    _policy_cache: dict[int, str] = {}

    def __init__(self, db: Session) -> None:
        self.db = db
        self.ssh = SshAndProviderRecoveryTransport()

    @classmethod
    def cached_policy(cls, assignment_id: int) -> str | None:
        with cls._cache_lock:
            return cls._policy_cache.get(assignment_id)

    @classmethod
    def _remember_policy(cls, assignment_id: int, policy: str) -> None:
        with cls._cache_lock:
            cls._policy_cache[assignment_id] = policy

    @staticmethod
    def assignment_id_from_import_text(import_text: str) -> int | None:
        line = next(
            (item.strip() for item in str(import_text or "").splitlines() if item.strip().startswith("vless://")),
            "",
        )
        if not line:
            return None
        try:
            parsed = urlsplit(line)
            raw = parse_qs(parsed.query).get("eg_assignment", [""])[0]
            value = int(raw)
            return value if value > 0 else None
        except (TypeError, ValueError):
            return None

    def apply_from_import_text(self, import_text: str, requested_policy: str) -> None:
        policy = (requested_policy or "").strip().lower()
        if policy not in self.VALID:
            raise HTTPException(status_code=400, detail="invalid_traffic_policy")
        assignment_id = self.assignment_id_from_import_text(import_text)
        if assignment_id is None:
            raise HTTPException(status_code=409, detail="per_device_policy_unavailable")
        self.apply(assignment_id, policy)

    def apply(self, assignment_id: int, requested_policy: str) -> None:
        policy = (requested_policy or "").strip().lower()
        if policy not in self.VALID:
            raise HTTPException(status_code=400, detail="invalid_traffic_policy")
        assignment = self.db.get(VpnAssignment, assignment_id)
        if assignment is None:
            raise HTTPException(status_code=404, detail="assignment_not_found")
        node = self.db.get(VpnNode, assignment.node_id)
        if node is None:
            raise HTTPException(status_code=409, detail="assigned_node_missing")

        # Re-assert the mode from the current request every time. Cache is not
        # trusted for enforcement, so backend restarts cannot restore stale state.
        result = self._apply_remote(node, assignment, policy)
        if not result["ok"]:
            raise HTTPException(status_code=503, detail=result["detail"])

        # Do not persist policy in the assignment, audit log, or database.
        self._remember_policy(assignment.id, policy)

    def _apply_remote(self, node: VpnNode, assignment: VpnAssignment, policy: str) -> dict:
        payload = json.dumps(
            {
                "assignment_id": assignment.id,
                "traffic_policy": policy,
                "config_path": settings.xray_config_path,
            },
            separators=(",", ":"),
        )
        script = self._remote_script(payload)
        client = None
        try:
            client = self.ssh._connect(node)
            stdin, stdout, stderr = client.exec_command("python3 -", timeout=180)
            stdin.write(script)
            stdin.flush()
            stdin.channel.shutdown_write()
            out = stdout.read().decode(errors="ignore").strip()
            err = stderr.read().decode(errors="ignore").strip()
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                logger.warning(
                    "traffic policy update failed assignment=%s node=%s rc=%s err=%s",
                    assignment.id,
                    node.id,
                    rc,
                    err[:200],
                )
                return {"ok": False, "detail": "traffic_policy_remote_failed"}
            try:
                parsed = json.loads(out or "{}")
            except json.JSONDecodeError:
                parsed = {}
            if parsed.get("ok") is not True:
                return {"ok": False, "detail": str(parsed.get("detail") or "traffic_policy_not_applied")[:120]}
            return {
                "ok": True,
                "detail": "traffic_policy_applied",
                "changed": parsed.get("changed") is True,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "traffic policy SSH failed assignment=%s node=%s err=%s",
                assignment.id,
                node.id,
                type(exc).__name__,
            )
            return {"ok": False, "detail": f"traffic_policy_ssh_failed:{type(exc).__name__}"}
        finally:
            if client is not None:
                client.close()

    @staticmethod
    def _remote_script(payload_json: str) -> str:
        encoded = json.dumps(payload_json)
        return '''import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request

DATA = json.loads(__PAYLOAD__)
assignment_id = int(DATA["assignment_id"])
policy = str(DATA["traffic_policy"])
path = str(DATA["config_path"])
tag_prefix = "emery-device-%d-" % assignment_id
# The dynamic source remains the registry-backed antifilter feed. Explicit
# service domains below are a fail-safe for major services whose access has
# been publicly restricted by Roskomnadzor.
RU_SERVICE_DOMAINS = [
    "domain:facebook.com",
    "domain:fb.com",
    "domain:fbcdn.net",
    "domain:messenger.com",
    "domain:instagram.com",
    "domain:cdninstagram.com",
    "domain:ig.me",
    "domain:x.com",
    "domain:twitter.com",
    "domain:twimg.com",
    "domain:t.co",
    "domain:linkedin.com",
    "domain:licdn.com",
    "domain:lnkd.in",
    "domain:discord.com",
    "domain:discord.gg",
    "domain:discordapp.com",
    "domain:discordapp.net",
    "domain:discordcdn.com",
    "domain:signal.org",
    "domain:signal.art",
    "domain:viber.com",
    "domain:viber.co",
    "domain:vb.me",
    "domain:youtube.com",
    "domain:youtu.be",
    "domain:youtube-nocookie.com",
    "domain:googlevideo.com",
    "domain:ytimg.com",
]
RU_DOMAINS = ["geosite:antifilter-download"] + RU_SERVICE_DOMAINS
RU_IPS = ["geoip:ru-blocked"]
ASSET_TTL_SECONDS = 6 * 60 * 60


def asset_is_fresh(target):
    try:
        stat = os.stat(target)
        return stat.st_size >= 16 * 1024 and (time.time() - stat.st_mtime) < ASSET_TTL_SECONDS
    except OSError:
        return False


def install_asset(name):
    asset_dir = "/usr/local/share/xray"
    os.makedirs(asset_dir, exist_ok=True)
    target = os.path.join(asset_dir, name)
    if asset_is_fresh(target):
        return
    bases = [
        "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release",
        "https://github.com/runetfreedom/russia-v2ray-rules-dat/releases/latest/download",
    ]
    last = None
    for base in bases:
        try:
            with urllib.request.urlopen(base + "/" + name + ".sha256sum", timeout=30) as response:
                checksum_text = response.read(4096).decode("utf-8", errors="ignore")
            match = re.search(r"(?i)\\b[0-9a-f]{64}\\b", checksum_text)
            if not match:
                raise RuntimeError("asset_checksum_invalid")
            expected = match.group(0).lower()
            with urllib.request.urlopen(base + "/" + name, timeout=120) as response:
                data = response.read(128 * 1024 * 1024 + 1)
            if len(data) < 16 * 1024 or len(data) > 128 * 1024 * 1024:
                raise RuntimeError("asset_size_invalid")
            if hashlib.sha256(data).hexdigest().lower() != expected:
                raise RuntimeError("asset_checksum_mismatch")
            fd, tmp = tempfile.mkstemp(prefix="." + name + ".", dir=asset_dir)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(tmp, 0o644)
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            return
        except Exception as exc:
            last = exc
    raise RuntimeError("policy_asset_download_failed:%s" % type(last).__name__)


if policy == "russia":
    install_asset("geosite.dat")
    install_asset("geoip.dat")

folder = os.path.dirname(path) or "."
lock = open(os.path.join(folder, ".emery-xray-policy.lock"), "a+", encoding="utf-8")
fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
with open(path, "r", encoding="utf-8") as handle:
    original = handle.read()
config = json.loads(original)

inbound_tag = ""
for inbound in list(config.get("inbounds") or []):
    tag = str(inbound.get("tag") or "")
    if tag.startswith(tag_prefix):
        inbound_tag = tag
        inbound["sniffing"] = {
            "enabled": True,
            "destOverride": ["http", "tls", "quic"],
            "routeOnly": True,
        }
        break
if not inbound_tag:
    raise RuntimeError("assignment_inbound_missing")

outbounds = list(config.get("outbounds") or [])
if not any(item.get("tag") == "emery-blocked" for item in outbounds):
    outbounds.append({"tag": "emery-blocked", "protocol": "blackhole"})
config["outbounds"] = outbounds

routing = config.setdefault("routing", {})
routing["domainStrategy"] = "IPIfNonMatch"
rules = []
for item in list(routing.get("rules") or []):
    inbound_tags = item.get("inboundTag") or []
    if isinstance(inbound_tags, str):
        inbound_tags = [inbound_tags]
    managed = any(str(value).startswith(tag_prefix) for value in inbound_tags)
    if not managed:
        rules.append(item)

if policy == "russia":
    rules = [
        {
            "type": "field",
            "inboundTag": [inbound_tag],
            "domain": RU_DOMAINS,
            "outboundTag": "emery-blocked",
        },
        {
            "type": "field",
            "inboundTag": [inbound_tag],
            "ip": RU_IPS,
            "outboundTag": "emery-blocked",
        },
    ] + rules
elif policy != "international":
    raise RuntimeError("invalid_traffic_policy")
routing["rules"] = rules

candidate_text = json.dumps(config, ensure_ascii=False, indent=2) + "\\n"
if candidate_text == original:
    print(json.dumps({"ok": True, "assignment_id": assignment_id, "changed": False}))
    raise SystemExit(0)

fd, candidate = tempfile.mkstemp(prefix=".emery-policy-", suffix=".json", dir=folder)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(candidate_text)
        handle.flush()
        os.fsync(handle.fileno())
    current_stat = os.stat(path, follow_symlinks=False)
    os.chown(candidate, current_stat.st_uid, current_stat.st_gid, follow_symlinks=False)
    os.chmod(candidate, current_stat.st_mode & 0o777, follow_symlinks=False)
    subprocess.run(["xray", "run", "-test", "-config", candidate], check=True, capture_output=True, text=True)
    os.replace(candidate, path)
    subprocess.run(["systemctl", "restart", "xray"], check=True, capture_output=True, text=True)
    subprocess.run(["systemctl", "is-active", "--quiet", "xray"], check=True)
except Exception:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(original)
        subprocess.run(["systemctl", "restart", "xray"], check=False, capture_output=True, text=True)
    finally:
        if os.path.exists(candidate):
            os.unlink(candidate)
    raise

print(json.dumps({"ok": True, "assignment_id": assignment_id, "changed": True}))
'''.replace("__PAYLOAD__", encoded)
