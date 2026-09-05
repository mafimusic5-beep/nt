from __future__ import annotations

import json
import logging
import re
import uuid

from src.backend.services.node_adapters import FirstVdsBillManagerProvisioningService
from src.common.config import settings
from src.common.models import VpnNode

logger = logging.getLogger(__name__)

_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


class ManualNodeBootstrapService:
    POLICY_RU = "ru"
    POLICY_INTL = "intl"

    @classmethod
    def resolve_policy(cls, region_code: str, requested_policy: str = "auto") -> str:
        requested = (requested_policy or "auto").strip().lower()
        if requested not in {"auto", cls.POLICY_RU, cls.POLICY_INTL}:
            raise ValueError("invalid_traffic_policy")
        region = (region_code or "").strip().lower()
        derived = cls.POLICY_RU if region == "ru" or region.startswith("ru-") else cls.POLICY_INTL
        if requested != "auto" and requested != derived:
            raise ValueError("traffic_policy_region_mismatch")
        return derived

    @classmethod
    def blocked_domains_for_policy(cls, policy: str) -> list[str]:
        if policy != cls.POLICY_RU:
            return []
        result: list[str] = []
        for value in settings.ru_policy_blocked_domain_list:
            domain = value.strip().lower().rstrip(".")
            if not domain:
                continue
            if not _DOMAIN_RE.fullmatch(domain):
                raise ValueError("ru_blocklist_invalid_domain")
            if domain not in result:
                result.append(domain)
        if not result:
            raise ValueError("ru_blocklist_not_configured")
        return result

    def bootstrap_with_password(
        self,
        node: VpnNode,
        *,
        ssh_user: str,
        ssh_password: str,
        policy: str,
    ) -> dict:
        endpoint = (node.endpoint or "").strip()
        username = (ssh_user or "").strip()
        password = ssh_password or ""
        if not endpoint:
            return {"status": "failed", "detail": "missing_endpoint"}
        if username != "root":
            return {"status": "failed", "detail": "manual_bootstrap_requires_root"}
        if not password:
            return {"status": "failed", "detail": "ssh_password_required"}

        try:
            blocked_domains = self.blocked_domains_for_policy(policy)
        except ValueError as exc:
            return {"status": "failed", "detail": str(exc)}

        adapter = FirstVdsBillManagerProvisioningService()
        adapter._ensure_node_ssh_keypair(node)
        script = self._bootstrap_script(
            port=settings.manual_bootstrap_vless_port,
            server_name=settings.manual_bootstrap_reality_sni,
            node_public_key=node.ssh_public_key,
            neutral_hostname=f"server-{node.id}",
            blocked_domains=blocked_domains,
        )

        try:
            import paramiko  # type: ignore
        except ImportError:
            return {"status": "failed", "detail": "paramiko_not_installed"}

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=endpoint,
                username=username,
                password=password,
                timeout=settings.manual_bootstrap_ssh_timeout_seconds,
                banner_timeout=settings.manual_bootstrap_ssh_timeout_seconds,
                auth_timeout=settings.manual_bootstrap_ssh_timeout_seconds,
                look_for_keys=False,
                allow_agent=False,
            )
            result = adapter._run_remote_script_via_paramiko_client(
                client,
                script,
                ok_detail="manual_ssh_bootstrap_ok",
                fail_detail="manual_ssh_bootstrap_failed",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "manual bootstrap SSH failed: node_id=%s endpoint=%s error=%s",
                node.id,
                endpoint,
                type(exc).__name__,
            )
            return {"status": "failed", "detail": f"ssh_connect_failed:{type(exc).__name__}"}
        finally:
            client.close()

        if result.get("status") != "ok":
            return {
                "status": "failed",
                "detail": str(result.get("detail") or "manual_ssh_bootstrap_failed")[:120],
                "stderr": str(result.get("stderr") or "")[:200],
            }

        values: dict[str, str] = {}
        for line in str(result.get("stdout") or "").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"XRAY_UUID", "XRAY_PUBLIC_KEY", "XRAY_SHORT_ID"}:
                values[key] = value.strip()
        if not all(values.get(key) for key in ("XRAY_UUID", "XRAY_PUBLIC_KEY", "XRAY_SHORT_ID")):
            return {"status": "failed", "detail": "bootstrap_values_missing"}

        tag = f"server-{node.id}-{uuid.uuid4().hex[:6]}"
        config_payload = adapter.normalize_config_payload(
            f"vless://{values['XRAY_UUID']}@{endpoint}:{settings.manual_bootstrap_vless_port}"
            f"?encryption=none&security=reality&sni={settings.manual_bootstrap_reality_sni}"
            f"&fp=chrome&pbk={values['XRAY_PUBLIC_KEY']}&sid={values['XRAY_SHORT_ID']}&type=tcp#{tag}"
        )
        if not adapter.is_config_payload_valid(config_payload):
            return {"status": "failed", "detail": "generated_vless_config_invalid"}

        node.config_payload = config_payload
        node.ssh_key_status = "installed" if node.ssh_public_key else node.ssh_key_status
        pinned_host_key = adapter._capture_ssh_host_key(endpoint, node.ssh_private_key)
        if pinned_host_key:
            node.ssh_host_key = pinned_host_key

        return {
            "status": "ok",
            "detail": "manual_node_bootstrapped",
            "policy": policy,
            "blocked_domains": len(blocked_domains),
            "config_payload": config_payload,
            "ssh_host_key_pinned": bool(node.ssh_host_key),
        }

    @staticmethod
    def _bootstrap_script(
        *,
        port: int,
        server_name: str,
        node_public_key: str,
        neutral_hostname: str,
        blocked_domains: list[str],
    ) -> str:
        escaped_public_key = node_public_key.replace("'", "'\\''") if node_public_key else ""
        authorized_keys_block = ""
        if escaped_public_key:
            authorized_keys_block = f"""
mkdir -p /root/.ssh
chmod 700 /root/.ssh
AUTH_KEY='{escaped_public_key}'
touch /root/.ssh/authorized_keys
grep -qxF \"$AUTH_KEY\" /root/.ssh/authorized_keys || printf '%s\n' \"$AUTH_KEY\" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
"""

        rules: list[dict] = []
        if blocked_domains:
            rules.append(
                {
                    "type": "field",
                    "domain": [f"domain:{domain}" for domain in blocked_domains],
                    "outboundTag": "blocked",
                }
            )
        rules.extend(
            [
                {"type": "field", "port": "25,465,587", "outboundTag": "blocked"},
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
            ]
        )
        rules_json = json.dumps(rules, ensure_ascii=True, separators=(",", ":"))
        hostname_json = json.dumps(neutral_hostname)
        reality_dest_json = json.dumps(f"{server_name}:443")
        server_name_json = json.dumps(server_name)

        return f"""#!/usr/bin/env bash
set -euo pipefail
[[ \"$(id -u)\" -eq 0 ]]
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y curl unzip openssl ca-certificates nftables python3
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y curl unzip openssl ca-certificates nftables python3
elif command -v yum >/dev/null 2>&1; then
  yum install -y curl unzip openssl ca-certificates nftables python3
fi
hostnamectl set-hostname {hostname_json} >/dev/null 2>&1 || true
{authorized_keys_block}
bash <(curl -fsSL --retry 3 https://github.com/XTLS/Xray-install/raw/main/install-release.sh) install
UUID=\"$(cat /proc/sys/kernel/random/uuid)\"
KEYS=\"$(xray x25519)\"
PRIVATE_KEY=\"$(printf '%s\n' \"$KEYS\" | awk -F': *' '/^Private[Kk]ey:/{{print $2; exit}}')\"
PUBLIC_KEY=\"$(printf '%s\n' \"$KEYS\" | awk -F': *' '/^Public key:/{{print $2; exit}} /^Password( \\(PublicKey\\))?:/{{print $2; exit}}')\"
SHORT_ID=\"$(openssl rand -hex 8)\"
[[ -n \"$PRIVATE_KEY\" && -n \"$PUBLIC_KEY\" && -n \"$SHORT_ID\" ]]
mkdir -p /usr/local/etc/xray
cat >/usr/local/etc/xray/config.json <<EOF
{{
  \"log\": {{\"loglevel\": \"warning\"}},
  \"inbounds\": [
    {{
      \"listen\": \"0.0.0.0\",
      \"port\": {int(port)},
      \"protocol\": \"vless\",
      \"settings\": {{
        \"clients\": [{{\"id\": \"$UUID\", \"flow\": \"xtls-rprx-vision\"}}],
        \"decryption\": \"none\"
      }},
      \"sniffing\": {{\"enabled\": true, \"destOverride\": [\"http\", \"tls\", \"quic\"], \"routeOnly\": true}},
      \"streamSettings\": {{
        \"network\": \"tcp\",
        \"security\": \"reality\",
        \"realitySettings\": {{
          \"show\": false,
          \"dest\": {reality_dest_json},
          \"xver\": 0,
          \"serverNames\": [{server_name_json}],
          \"privateKey\": \"$PRIVATE_KEY\",
          \"shortIds\": [\"$SHORT_ID\"]
        }}
      }}
    }}
  ],
  \"outbounds\": [
    {{\"tag\": \"direct\", \"protocol\": \"freedom\"}},
    {{\"tag\": \"blocked\", \"protocol\": \"blackhole\"}}
  ],
  \"routing\": {{
    \"domainStrategy\": \"IPIfNonMatch\",
    \"rules\": {rules_json}
  }}
}}
EOF
xray run -test -config /usr/local/etc/xray/config.json
systemctl enable xray >/dev/null 2>&1 || true
systemctl restart xray
systemctl is-active xray >/dev/null 2>&1
printf 'XRAY_UUID=%s\n' \"$UUID\"
printf 'XRAY_PUBLIC_KEY=%s\n' \"$PUBLIC_KEY\"
printf 'XRAY_SHORT_ID=%s\n' \"$SHORT_ID\"
"""
