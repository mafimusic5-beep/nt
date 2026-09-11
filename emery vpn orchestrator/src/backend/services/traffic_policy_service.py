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

    # Process-local cache is only an optimization. Enforcement state is also
    # persisted on the VPN node so a backend restart cannot forget other users'
    # policy while rebuilding the node-wide grouped routing rules.
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

        result = self._apply_remote(node, assignment, policy)
        if not result["ok"]:
            raise HTTPException(status_code=503, detail=result["detail"])

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
                    err[-3000:],
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
                "hot_reloaded": parsed.get("hot_reloaded") is True,
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
folder = os.path.dirname(path) or "."
state_path = os.path.join(folder, ".emery-traffic-policies.json")
API_LISTEN = "127.0.0.1:10085"
POLICY_RULE_PREFIX = "skryon-policy-"
VALID_POLICIES = {"international", "russia"}

# Keep the broad IP block list server-side, but never load the giant Russia
# geosite.dat on small 1 GB VPN nodes. A single shared GeoIP matcher plus a
# compact explicit domain set is enough for every Russia-policy assignment on
# the node; the heavy list is not duplicated per user.
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
RU_IPS = ["ext:ru-geoip.dat:ru-blocked"]
PRIVATE_NETWORKS = [
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
]
ASSET_TTL_SECONDS = 6 * 60 * 60


def asset_is_fresh(target):
    try:
        stat = os.stat(target)
        return stat.st_size >= 16 * 1024 and (time.time() - stat.st_mtime) < ASSET_TTL_SECONDS
    except OSError:
        return False


def install_asset(source_name, target_name):
    asset_dir = "/usr/local/share/xray"
    os.makedirs(asset_dir, exist_ok=True)
    target = os.path.join(asset_dir, target_name)
    if asset_is_fresh(target):
        return
    bases = [
        "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release",
        "https://github.com/runetfreedom/russia-v2ray-rules-dat/releases/latest/download",
    ]
    last = None
    for base in bases:
        try:
            with urllib.request.urlopen(base + "/" + source_name + ".sha256sum", timeout=30) as response:
                checksum_text = response.read(4096).decode("utf-8", errors="ignore")
            match = re.search(r"(?i)\\b[0-9a-f]{64}\\b", checksum_text)
            if not match:
                raise RuntimeError("asset_checksum_invalid")
            expected = match.group(0).lower()
            with urllib.request.urlopen(base + "/" + source_name, timeout=120) as response:
                data = response.read(128 * 1024 * 1024 + 1)
            if len(data) < 16 * 1024 or len(data) > 128 * 1024 * 1024:
                raise RuntimeError("asset_size_invalid")
            if hashlib.sha256(data).hexdigest().lower() != expected:
                raise RuntimeError("asset_checksum_mismatch")
            fd, tmp = tempfile.mkstemp(prefix="." + target_name + ".", dir=asset_dir)
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


def load_policy_state():
    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    clean = {}
    for key, value in raw.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        normalized = str(value or "").strip().lower()
        if number > 0 and normalized in VALID_POLICIES:
            clean[str(number)] = normalized
    return clean


def write_policy_state(state):
    fd, temporary = tempfile.mkstemp(prefix=".emery-policy-state-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + chr(10))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, state_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if policy not in VALID_POLICIES:
    raise RuntimeError("invalid_traffic_policy")

lock = open(os.path.join(folder, ".emery-xray-policy.lock"), "a+", encoding="utf-8")
fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
with open(path, "r", encoding="utf-8") as handle:
    original = handle.read()
config = json.loads(original)

# Discover every active assignment inbound. Policies are grouped by inboundTag,
# so Xray keeps a constant number of matchers/rules as the user count grows.
managed_by_assignment = {}
inbounds_changed = False
for inbound in list(config.get("inbounds") or []):
    tag = str(inbound.get("tag") or "")
    match = re.match(r"^emery-device-(\\d+)-", tag)
    if not match:
        continue
    managed_by_assignment[int(match.group(1))] = tag
    desired_sniffing = {
        "enabled": True,
        "destOverride": ["http", "tls", "quic"],
        "routeOnly": True,
    }
    if inbound.get("sniffing") != desired_sniffing:
        inbound["sniffing"] = desired_sniffing
        inbounds_changed = True

if assignment_id not in managed_by_assignment:
    raise RuntimeError("assignment_inbound_missing")

policies = load_policy_state()
active_ids = set(managed_by_assignment)
policies = {
    str(key): value
    for key, value in (
        (int(raw_key), raw_value)
        for raw_key, raw_value in policies.items()
        if str(raw_key).isdigit()
    )
    if key in active_ids and value in VALID_POLICIES
}
policies[str(assignment_id)] = policy
all_tags = sorted(managed_by_assignment.values())
russia_tags = sorted(
    tag
    for current_id, tag in managed_by_assignment.items()
    if policies.get(str(current_id), "international") == "russia"
)

# Only the compact GeoIP asset is needed, and only while at least one assignment
# uses Russia policy. Xray's GeoIP registry shares the resulting IP set.
if russia_tags:
    install_asset("geoip.dat", "ru-geoip.dat")

outbounds = list(config.get("outbounds") or [])
outbounds_before = json.dumps(outbounds, sort_keys=True, separators=(",", ":"))
if not any(item.get("tag") == "direct" for item in outbounds):
    outbounds.append({"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}})
if not any(item.get("tag") == "emery-blocked" for item in outbounds):
    outbounds.append({"tag": "emery-blocked", "protocol": "blackhole"})
config["outbounds"] = outbounds
outbounds_changed = outbounds_before != json.dumps(outbounds, sort_keys=True, separators=(",", ":"))

# Xray 1.8.12+ supports a loopback-only simplified API listener. RoutingService
# lets us replace the grouped rules in-place; normal policy switches therefore
# do not start a second Xray process and do not restart the live VPN service.
existing_api = config.get("api")
services = []
if isinstance(existing_api, dict):
    services = [str(value) for value in list(existing_api.get("services") or []) if str(value)]
if "RoutingService" not in services:
    services.append("RoutingService")
desired_api = {
    "tag": "api",
    "listen": API_LISTEN,
    "services": services,
}
api_changed = existing_api != desired_api
config["api"] = desired_api

routing = config.setdefault("routing", {})
routing["domainStrategy"] = "IPIfNonMatch"
preserved_rules = []
for item in list(routing.get("rules") or []):
    inbound_tags = item.get("inboundTag") or []
    if isinstance(inbound_tags, str):
        inbound_tags = [inbound_tags]
    touches_managed = any(str(value).startswith("emery-device-") for value in inbound_tags)
    if touches_managed or str(item.get("ruleTag") or "").startswith(POLICY_RULE_PREFIX):
        continue

    # Migrate historical stock-GeoIP private references because older Russia
    # policy versions may have replaced geoip.dat on this node.
    ip_values = item.get("ip")
    if isinstance(ip_values, list) and "geoip:private" in ip_values:
        migrated = []
        for value in ip_values:
            if value == "geoip:private":
                migrated.extend(PRIVATE_NETWORKS)
            else:
                migrated.append(value)
        item = dict(item)
        item["ip"] = migrated
    preserved_rules.append(item)

# Five node-wide rules at most, regardless of whether there are 1, 20, or 100
# assignments. The per-assignment inbound is retained for device isolation and
# the existing kernel/nft per-user speed limit.
managed_rules = []
if all_tags:
    managed_rules.extend(
        [
            {
                "type": "field",
                "ruleTag": POLICY_RULE_PREFIX + "smtp",
                "inboundTag": all_tags,
                "port": "25,465,587",
                "outboundTag": "emery-blocked",
            },
            {
                "type": "field",
                "ruleTag": POLICY_RULE_PREFIX + "private",
                "inboundTag": all_tags,
                "ip": PRIVATE_NETWORKS,
                "outboundTag": "emery-blocked",
            },
        ]
    )
if russia_tags:
    managed_rules.extend(
        [
            {
                "type": "field",
                "ruleTag": POLICY_RULE_PREFIX + "russia-domains",
                "inboundTag": russia_tags,
                "domain": RU_SERVICE_DOMAINS,
                "outboundTag": "emery-blocked",
            },
            {
                "type": "field",
                "ruleTag": POLICY_RULE_PREFIX + "russia-ips",
                "inboundTag": russia_tags,
                "ip": RU_IPS,
                "outboundTag": "emery-blocked",
            },
        ]
    )
if all_tags:
    managed_rules.append(
        {
            "type": "field",
            "ruleTag": POLICY_RULE_PREFIX + "direct",
            "inboundTag": all_tags,
            "outboundTag": "direct",
        }
    )
routing["rules"] = managed_rules + preserved_rules

candidate_text = json.dumps(config, ensure_ascii=False, indent=2) + chr(10)
state_text = json.dumps(policies, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + chr(10)
try:
    with open(state_path, "r", encoding="utf-8") as handle:
        current_state_text = handle.read()
except OSError:
    current_state_text = ""

if candidate_text == original and state_text == current_state_text:
    print(json.dumps({"ok": True, "assignment_id": assignment_id, "changed": False, "hot_reloaded": False}))
    raise SystemExit(0)

static_changed = api_changed or inbounds_changed or outbounds_changed
current_stat = os.stat(path, follow_symlinks=False)
fd, candidate = tempfile.mkstemp(prefix=".emery-policy-", suffix=".json", dir=folder)
routing_fd, routing_candidate = tempfile.mkstemp(prefix=".emery-routing-", suffix=".json", dir=folder)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(candidate_text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(candidate, current_stat.st_uid, current_stat.st_gid, follow_symlinks=False)
    os.chmod(candidate, current_stat.st_mode & 0o777, follow_symlinks=False)

    with os.fdopen(routing_fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"routing": routing}, ensure_ascii=False, separators=(",", ":")) + chr(10))
        handle.flush()
        os.fsync(handle.fileno())

    if static_changed:
        # One restart is required only when installing the local RoutingService
        # API or repairing static inbound/outbound structure. Ordinary policy
        # changes take the hot path below.
        validation = subprocess.run(
            ["xray", "run", "-test", "-config", candidate],
            check=False,
            capture_output=True,
            text=True,
        )
        if validation.returncode != 0:
            detail = " | ".join(
                ((validation.stderr or "") + chr(10) + (validation.stdout or "") or "xray_config_invalid")
                .strip()
                .splitlines()
            )
            raise RuntimeError("xray_config_invalid:" + detail[-3000:])
        os.replace(candidate, path)
        try:
            subprocess.run(["systemctl", "restart", "xray"], check=True, capture_output=True, text=True)
            subprocess.run(["systemctl", "is-active", "--quiet", "xray"], check=True)
        except Exception:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(original)
            subprocess.run(["systemctl", "restart", "xray"], check=False, capture_output=True, text=True)
            raise
        write_policy_state(policies)
        hot_reloaded = False
    else:
        # Replace the complete routing table in the running Xray process. This
        # avoids both a second validation process and a VPN disconnect/restart.
        hot = subprocess.run(
            ["xray", "api", "adrules", "--server=" + API_LISTEN, routing_candidate],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if hot.returncode != 0:
            detail = " | ".join(((hot.stderr or "") + chr(10) + (hot.stdout or "")).strip().splitlines())
            raise RuntimeError("xray_routing_hot_reload_failed:" + detail[-2000:])
        os.replace(candidate, path)
        write_policy_state(policies)
        hot_reloaded = True

    # Old heavyweight geosite assets are never referenced by this policy.
    try:
        os.unlink("/usr/local/share/xray/ru-geosite.dat")
    except OSError:
        pass
finally:
    if os.path.exists(candidate):
        os.unlink(candidate)
    if os.path.exists(routing_candidate):
        os.unlink(routing_candidate)

print(json.dumps({
    "ok": True,
    "assignment_id": assignment_id,
    "changed": True,
    "hot_reloaded": hot_reloaded,
    "managed_assignments": len(all_tags),
    "russia_assignments": len(russia_tags),
}))
'''.replace("__PAYLOAD__", encoded)
