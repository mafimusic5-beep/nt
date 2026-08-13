from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from src.backend.services.node_recovery_service import SshAndProviderRecoveryTransport
from src.common.config import settings
from src.common.models import VpnAssignment, VpnNode

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CredentialMutationResult:
    ok: bool
    detail: str
    rate_limit_enforced: bool = False
    smtp_block_enforced: bool = False
    shared_credential_disabled: bool = False


class XrayCredentialTransport(Protocol):
    def install(self, node: VpnNode, assignment: VpnAssignment) -> CredentialMutationResult: ...

    def remove(self, node: VpnNode, assignment: VpnAssignment) -> CredentialMutationResult: ...


class VlessDeviceConfigBuilder:
    @staticmethod
    def build(node: VpnNode, assignment: VpnAssignment) -> str:
        source = next(
            (
                line.strip()
                for line in (node.config_payload or "").splitlines()
                if line.strip().startswith("vless://")
            ),
            "",
        )
        if not source:
            return ""
        try:
            parsed = urlsplit(source)
        except ValueError:
            return ""
        if not parsed.hostname:
            return ""
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        netloc = f"{assignment.client_uuid}@{host}:{assignment.client_port}"
        fragment = parsed.fragment or f"{node.region_code}-{node.id}"
        return urlunsplit(("vless", netloc, parsed.path, parsed.query, fragment))


class ScriptOrSshXrayCredentialTransport:
    """Install one UUID on one dedicated port and enforce its server-side cap."""

    def __init__(self) -> None:
        self.ssh = SshAndProviderRecoveryTransport()

    @staticmethod
    def _payload(action: str, node: VpnNode, assignment: VpnAssignment) -> dict:
        return {
            "action": action,
            "node_id": node.id,
            "provider": node.provider,
            "provider_server_id": node.provider_server_id or node.firstvds_vps_id,
            "endpoint": node.endpoint,
            "config_path": settings.xray_config_path,
            "assignment_id": assignment.id,
            "client_uuid": assignment.client_uuid,
            "client_port": assignment.client_port,
            "speed_limit_mbps": assignment.speed_limit_mbps,
        }

    def _external(
        self,
        action: str,
        node: VpnNode,
        assignment: VpnAssignment,
    ) -> CredentialMutationResult | None:
        script = (settings.xray_credential_script or "").strip()
        if not script:
            return None
        try:
            result = subprocess.run(
                [script, json.dumps(self._payload(action, node, assignment), ensure_ascii=False)],
                capture_output=True,
                text=True,
                check=False,
                timeout=max(int(settings.xray_credential_timeout_seconds), 10),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CredentialMutationResult(False, f"credential_script_failed:{type(exc).__name__}")
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            payload = {}
        ok = result.returncode == 0 and bool(payload.get("ok"))
        return CredentialMutationResult(
            ok=ok,
            detail=str(payload.get("detail") or result.stderr or f"exit_{result.returncode}")[:500],
            rate_limit_enforced=bool(payload.get("rate_limit_enforced")),
            smtp_block_enforced=bool(payload.get("smtp_block_enforced")),
            shared_credential_disabled=bool(payload.get("shared_credential_disabled")),
        )

    @staticmethod
    def _remote_script(action: str, assignment: VpnAssignment) -> str:
        payload = {
            "action": action,
            "config_path": settings.xray_config_path,
            "assignment_id": assignment.id,
            "client_uuid": assignment.client_uuid,
            "client_port": assignment.client_port,
            "speed_limit_mbps": assignment.speed_limit_mbps,
        }
        encoded = json.dumps(json.dumps(payload, separators=(",", ":")))
        template = '''import copy
import fcntl
import json
import os
import subprocess
import tempfile

DATA = json.loads(__EMERY_PAYLOAD__)
path = DATA["config_path"]
directory = os.path.dirname(path) or "."
# Credential mutations can arrive from different API workers. Serialize the
# complete read/validate/replace/restart transaction on the VPS so one device
# cannot overwrite another device's concurrently installed inbound.
lock_handle = open(os.path.join(directory, ".emery-xray-credentials.lock"), "a+", encoding="utf-8")
fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
with open(path, "r", encoding="utf-8") as handle:
    original_text = handle.read()
config = json.loads(original_text)
original_config = json.loads(original_text)
tag_prefix = "emery-device-%s-" % DATA["assignment_id"]
tag = tag_prefix + str(DATA["speed_limit_mbps"])
inbounds = list(config.get("inbounds") or [])

if DATA["action"] == "upsert_client":
    base = next((item for item in inbounds if item.get("protocol") == "vless" and not str(item.get("tag", "")).startswith("emery-device-")), None)
    if base is None:
        raise RuntimeError("base_vless_inbound_missing")
    dedicated = copy.deepcopy(base)
    dedicated["tag"] = tag
    dedicated["port"] = int(DATA["client_port"])
    settings_block = dedicated.setdefault("settings", {})
    base_clients = list(settings_block.get("clients") or [])
    flow = str(base_clients[0].get("flow", "xtls-rprx-vision")) if base_clients else "xtls-rprx-vision"
    settings_block["clients"] = [{
        "id": DATA["client_uuid"],
        "flow": flow,
        "email": tag,
    }]
    # The base inbound is a template/listener only.  Its historical shared UUID
    # must stop authenticating as soon as unique-device mode is activated.
    base.setdefault("settings", {})["clients"] = []
    inbounds = [item for item in inbounds if not str(item.get("tag", "")).startswith(tag_prefix)]
    inbounds.append(dedicated)
elif DATA["action"] == "remove_client":
    inbounds = [item for item in inbounds if not str(item.get("tag", "")).startswith(tag_prefix)]
else:
    raise RuntimeError("unsupported_action")

config["inbounds"] = inbounds
outbounds = list(config.get("outbounds") or [])
if not any(item.get("tag") == "emery-blocked" for item in outbounds):
    outbounds.append({"tag": "emery-blocked", "protocol": "blackhole"})
config["outbounds"] = outbounds
routing = config.setdefault("routing", {})
rules = [
    item for item in list(routing.get("rules") or [])
    if not (
        item.get("outboundTag") == "emery-blocked"
        and (item.get("port") in {"25", "25,465,587"} or item.get("ip") == ["geoip:private"])
    )
]
routing["rules"] = [
    {"type": "field", "port": "25,465,587", "outboundTag": "emery-blocked"},
    {"type": "field", "ip": ["geoip:private"], "outboundTag": "emery-blocked"},
] + rules

def managed_rows(config_value):
    managed = []
    for item in list(config_value.get("inbounds") or []):
        inbound_tag = str(item.get("tag", ""))
        if not inbound_tag.startswith("emery-device-"):
            continue
        try:
            speed = int(inbound_tag.rsplit("-", 1)[1])
            port = int(item["port"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("managed_inbound_invalid")
        managed.append((port, speed))
    return sorted(set(managed))

def nft_rules(rows, table_name):
    lines = [
        "table inet %s {" % table_name,
        " chain ingress { type filter hook input priority filter; policy accept;",
    ]
    for port, speed in rows:
        rate = max(1, speed * 1000 // 8)
        burst = max(1, rate // 5)
        lines.append("  tcp dport %d limit rate over %d kbytes/second burst %d kbytes counter drop" % (port, rate, burst))
    lines.extend([" }", " chain egress { type filter hook output priority filter; policy accept;"])
    for port, speed in rows:
        rate = max(1, speed * 1000 // 8)
        burst = max(1, rate // 5)
        lines.append("  tcp sport %d limit rate over %d kbytes/second burst %d kbytes counter drop" % (port, rate, burst))
    lines.extend([" }", "}"])
    return "\\n".join(lines) + "\\n"

fd, candidate_path = tempfile.mkstemp(prefix=".emery-xray-", suffix=".json", dir=directory)
old_rows = managed_rows(original_config)
new_rows = managed_rows(config)
old_nft = nft_rules(old_rows, "emery_vpn_rate")
new_nft = nft_rules(new_rows, "emery_vpn_rate")
check_nft = nft_rules(new_rows, "emery_vpn_rate_check")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\\n")
    subprocess.run(["xray", "run", "-test", "-config", candidate_path], check=True, capture_output=True, text=True)
    subprocess.run(["nft", "-c", "-f", "-"], input=check_nft, check=True, capture_output=True, text=True)
    subprocess.run(["nft", "delete", "table", "inet", "emery_vpn_rate"], check=False, capture_output=True, text=True)
    subprocess.run(["nft", "-f", "-"], input=new_nft, check=True, capture_output=True, text=True)
    os.chmod(candidate_path, 0o600)
    os.replace(candidate_path, path)
    subprocess.run(["systemctl", "restart", "xray"], check=True, capture_output=True, text=True)
    subprocess.run(["systemctl", "is-active", "--quiet", "xray"], check=True)
except Exception:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(original_text)
        subprocess.run(["nft", "delete", "table", "inet", "emery_vpn_rate"], check=False, capture_output=True, text=True)
        subprocess.run(["nft", "-f", "-"], input=old_nft, check=False, capture_output=True, text=True)
        subprocess.run(["systemctl", "restart", "xray"], check=False, capture_output=True, text=True)
    finally:
        if os.path.exists(candidate_path):
            os.unlink(candidate_path)
        raise

print(json.dumps({"ok": True, "rate_limit_enforced": True, "smtp_block_enforced": True, "shared_credential_disabled": True}))
'''
        return template.replace("__EMERY_PAYLOAD__", encoded)

    def _ssh(
        self,
        action: str,
        node: VpnNode,
        assignment: VpnAssignment,
    ) -> CredentialMutationResult:
        client = None
        try:
            client = self.ssh._connect(node)
            stdin, stdout, stderr = client.exec_command(
                "python3 -",
                timeout=max(int(settings.xray_credential_timeout_seconds), 10),
            )
            stdin.write(self._remote_script(action, assignment))
            stdin.flush()
            stdin.channel.shutdown_write()
            out = stdout.read().decode(errors="ignore").strip()
            err = stderr.read().decode(errors="ignore").strip()
            rc = stdout.channel.recv_exit_status()
            try:
                payload = json.loads(out.splitlines()[-1] if out else "{}")
            except json.JSONDecodeError:
                payload = {}
            ok = rc == 0 and bool(payload.get("ok"))
            return CredentialMutationResult(
                ok=ok,
                detail=(str(payload.get("detail") or err or out or f"exit_{rc}"))[:500],
                rate_limit_enforced=bool(payload.get("rate_limit_enforced")),
                smtp_block_enforced=bool(payload.get("smtp_block_enforced")),
                shared_credential_disabled=bool(payload.get("shared_credential_disabled")),
            )
        except Exception as exc:  # noqa: BLE001
            return CredentialMutationResult(False, f"credential_ssh_failed:{type(exc).__name__}:{exc}")
        finally:
            if client is not None:
                client.close()

    def _mutate(
        self,
        action: str,
        node: VpnNode,
        assignment: VpnAssignment,
    ) -> CredentialMutationResult:
        external = self._external(action, node, assignment)
        result = external if external is not None else self._ssh(action, node, assignment)
        if action == "upsert_client" and result.ok:
            if not result.rate_limit_enforced:
                return CredentialMutationResult(False, "rate_limit_not_enforced")
            if not result.smtp_block_enforced:
                return CredentialMutationResult(False, "smtp_block_not_enforced")
            if not result.shared_credential_disabled:
                return CredentialMutationResult(False, "shared_credential_not_disabled")
        return result

    def install(self, node: VpnNode, assignment: VpnAssignment) -> CredentialMutationResult:
        return self._mutate("upsert_client", node, assignment)

    def remove(self, node: VpnNode, assignment: VpnAssignment) -> CredentialMutationResult:
        return self._mutate("remove_client", node, assignment)
