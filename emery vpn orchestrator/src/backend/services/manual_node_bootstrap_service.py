from __future__ import annotations

import json
import logging
import shlex
import uuid

from src.backend.services.manual_isp_egress_service import (
    ManualIspEgressPlan,
    ManualIspEgressService,
)
from src.backend.services.node_adapters import FirstVdsBillManagerProvisioningService
from src.common.config import settings
from src.common.models import VpnNode

logger = logging.getLogger(__name__)


class ManualNodeBootstrapService:
    """Prepare one VPS for the shared pool without assigning a regional policy.

    Regional policy is per device/assignment and is re-applied on every connect.
    The bootstrap installs the neutral Xray/Reality base, global abuse
    protections, the orchestrator SSH key, and—when configured—a unique
    WireGuard path through a controlled ISP egress.
    """

    def bootstrap_with_password(
        self,
        node: VpnNode,
        *,
        ssh_user: str,
        ssh_password: str,
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

        egress_service = ManualIspEgressService()
        try:
            egress_plan = egress_service.plan_for_node(int(node.id))
        except ValueError as exc:
            return {"status": "failed", "detail": str(exc)[:120]}

        adapter = FirstVdsBillManagerProvisioningService()
        adapter._ensure_node_ssh_keypair(node)
        script = self._bootstrap_script(
            port=settings.manual_bootstrap_vless_port,
            server_name=settings.manual_bootstrap_reality_sni,
            node_public_key=node.ssh_public_key,
            neutral_hostname=f"server-{node.id}",
            isp_egress=egress_plan,
        )

        try:
            import paramiko  # type: ignore
        except ImportError:
            return {"status": "failed", "detail": "paramiko_not_installed"}

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        values: dict[str, str] = {}
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
            if result.get("status") != "ok":
                return {
                    "status": "failed",
                    "detail": str(result.get("detail") or "manual_ssh_bootstrap_failed")[:120],
                    "stderr": str(result.get("stderr") or "")[:200],
                }

            for line in str(result.get("stdout") or "").splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key in {
                    "XRAY_UUID",
                    "XRAY_PUBLIC_KEY",
                    "XRAY_SHORT_ID",
                    "WG_PUBLIC_KEY",
                    "WG_ADDRESS",
                }:
                    values[key] = value.strip()

            required = {"XRAY_UUID", "XRAY_PUBLIC_KEY", "XRAY_SHORT_ID"}
            if egress_plan is not None:
                required.update({"WG_PUBLIC_KEY", "WG_ADDRESS"})
            if not all(values.get(key) for key in required):
                return {"status": "failed", "detail": "bootstrap_values_missing"}

            if egress_plan is not None:
                if values.get("WG_ADDRESS") != egress_plan.client_ip:
                    return {"status": "failed", "detail": "isp_egress_address_mismatch"}
                registered = egress_service.register_peer(
                    egress_plan,
                    values["WG_PUBLIC_KEY"],
                )
                if not registered.get("ok"):
                    return {
                        "status": "failed",
                        "detail": str(registered.get("detail") or "isp_egress_peer_registration_failed")[:120],
                    }
                if not self._verify_isp_egress(client, egress_plan):
                    return {"status": "failed", "detail": "isp_egress_handshake_failed"}
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
            "config_payload": config_payload,
            "policy_ready": True,
            "isp_egress_enabled": egress_plan is not None,
            "ssh_host_key_pinned": bool(node.ssh_host_key),
        }

    @staticmethod
    def _verify_isp_egress(client, plan: ManualIspEgressPlan) -> bool:
        iface = shlex.quote(plan.interface)
        gateway = shlex.quote(plan.gateway_ip)
        client_ip = shlex.quote(plan.client_ip)
        table = int(plan.routing_table)
        command = f"""set -euo pipefail
IFACE={iface}
GATEWAY={gateway}
CLIENT_IP={client_ip}
for _ in 1 2 3; do
  ping -4 -I \"$IFACE\" -c 1 -W 2 \"$GATEWAY\" >/dev/null 2>&1 || true
  sleep 1
  LATEST=\"$(wg show \"$IFACE\" latest-handshakes | awk 'NR==1 {{print $2}}')\"
  NOW=\"$(date +%s)\"
  if [[ -n \"$LATEST\" && \"$LATEST\" -gt 0 && $((NOW-LATEST)) -le 30 ]]; then
    break
  fi
done
LATEST=\"$(wg show \"$IFACE\" latest-handshakes | awk 'NR==1 {{print $2}}')\"
NOW=\"$(date +%s)\"
[[ -n \"$LATEST\" && \"$LATEST\" -gt 0 && $((NOW-LATEST)) -le 30 ]]
ip rule show | grep -F \"from $CLIENT_IP lookup {table}\" >/dev/null
ip route show table {table} | grep -E \"^default .* dev $IFACE( |$)\" >/dev/null
nft list table inet skryon_egress_killswitch >/dev/null
"""
        try:
            _stdin, stdout, stderr = client.exec_command("bash -lc " + shlex.quote(command), timeout=20)
            _ = stdout.read()
            err = stderr.read().decode(errors="ignore").strip()
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                logger.warning("ISP egress verification failed rc=%s err=%s", rc, err[:200])
            return rc == 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("ISP egress verification error=%s", type(exc).__name__)
            return False

    @staticmethod
    def _bootstrap_script(
        *,
        port: int,
        server_name: str,
        node_public_key: str,
        neutral_hostname: str,
        isp_egress: ManualIspEgressPlan | None = None,
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

        rules = [
            {"type": "field", "port": "25,465,587", "outboundTag": "emery-blocked"},
            {"type": "field", "ip": ["geoip:private"], "outboundTag": "emery-blocked"},
        ]
        direct_outbound: dict[str, object] = {"tag": "direct", "protocol": "freedom"}
        egress_setup = ""
        egress_packages_apt = ""
        egress_packages_rpm = ""
        egress_print = ""
        if isp_egress is not None:
            # Do not permit raw IPv6 to fall back to the VPS provider while the
            # ISP egress is IPv4-only. Domain destinations are resolved as IPv4
            # by the freedom outbound below.
            rules.insert(0, {"type": "field", "ip": ["::/0"], "outboundTag": "emery-blocked"})
            direct_outbound = {
                "tag": "direct",
                "protocol": "freedom",
                "sendThrough": isp_egress.client_ip,
                "settings": {"domainStrategy": "UseIPv4"},
            }
            egress_packages_apt = " wireguard-tools iproute2 iputils-ping"
            egress_packages_rpm = " wireguard-tools iproute iputils"
            iface = shlex.quote(isp_egress.interface)
            address = shlex.quote(isp_egress.client_ip)
            peer_endpoint = shlex.quote(isp_egress.peer_endpoint)
            peer_public_key = shlex.quote(isp_egress.peer_public_key)
            mtu = int(isp_egress.mtu)
            keepalive = int(isp_egress.keepalive_seconds)
            table = int(isp_egress.routing_table)
            egress_setup = f"""
WG_IFACE={iface}
WG_ADDR={address}
WG_PEER_ENDPOINT={peer_endpoint}
WG_PEER_PUBLIC_KEY={peer_public_key}
WG_MTU={mtu}
WG_TABLE={table}
WG_PRIVATE_KEY=\"$(wg genkey)\"
WG_PUBLIC_KEY=\"$(printf '%s' \"$WG_PRIVATE_KEY\" | wg pubkey)\"
[[ -n \"$WG_PRIVATE_KEY\" && -n \"$WG_PUBLIC_KEY\" ]]
install -d -m 0700 /etc/wireguard

cat >/usr/local/sbin/skryon-egress-route <<EOF
#!/usr/bin/env bash
set -euo pipefail
ACTION=\"\${{1:-}}\"
WG_IFACE='$WG_IFACE'
WG_ADDR='$WG_ADDR'
WG_TABLE='$WG_TABLE'
if [[ \"\$ACTION\" == \"up\" ]]; then
  ip rule del priority 10000 from \"\$WG_ADDR/32\" table \"\$WG_TABLE\" >/dev/null 2>&1 || true
  ip rule add priority 10000 from \"\$WG_ADDR/32\" table \"\$WG_TABLE\"
  ip route replace default dev \"\$WG_IFACE\" table \"\$WG_TABLE\"
  nft delete table inet skryon_egress_killswitch >/dev/null 2>&1 || true
  nft -f - <<NFT
 table inet skryon_egress_killswitch {{
   chain output {{
     type filter hook output priority filter; policy accept;
     ip saddr \$WG_ADDR oifname != \"\$WG_IFACE\" counter drop
   }}
 }}
NFT
elif [[ \"\$ACTION\" == \"down\" ]]; then
  ip rule del priority 10000 from \"\$WG_ADDR/32\" table \"\$WG_TABLE\" >/dev/null 2>&1 || true
  ip route flush table \"\$WG_TABLE\" >/dev/null 2>&1 || true
  # Keep the nft kill switch in place. If WireGuard is down, traffic carrying
  # the tunnel source address must fail closed rather than use the VPS uplink.
else
  exit 64
fi
EOF
chmod 0755 /usr/local/sbin/skryon-egress-route

cat >\"/etc/wireguard/$WG_IFACE.conf\" <<EOF
[Interface]
PrivateKey = $WG_PRIVATE_KEY
Address = $WG_ADDR/32
MTU = $WG_MTU
Table = off
PostUp = /usr/local/sbin/skryon-egress-route up
PostDown = /usr/local/sbin/skryon-egress-route down

[Peer]
PublicKey = $WG_PEER_PUBLIC_KEY
Endpoint = $WG_PEER_ENDPOINT
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = {keepalive}
EOF
chmod 0600 \"/etc/wireguard/$WG_IFACE.conf\"
systemctl enable --now \"wg-quick@$WG_IFACE.service\" >/dev/null
systemctl is-active \"wg-quick@$WG_IFACE.service\" >/dev/null

mkdir -p /etc/systemd/system/xray.service.d
cat >/etc/systemd/system/xray.service.d/20-skryon-egress.conf <<EOF
[Unit]
Requires=wg-quick@$WG_IFACE.service
After=wg-quick@$WG_IFACE.service
EOF
systemctl daemon-reload
"""
            egress_print = """
printf 'WG_PUBLIC_KEY=%s\n' \"$WG_PUBLIC_KEY\"
printf 'WG_ADDRESS=%s\n' \"$WG_ADDR\"
"""

        rules_json = json.dumps(rules, ensure_ascii=True, separators=(",", ":"))
        outbounds_json = json.dumps(
            [direct_outbound, {"tag": "emery-blocked", "protocol": "blackhole"}],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        hostname_json = json.dumps(neutral_hostname)
        reality_dest_json = json.dumps(f"{server_name}:443")
        server_name_json = json.dumps(server_name)

        return f"""#!/usr/bin/env bash
set -euo pipefail
[[ \"$(id -u)\" -eq 0 ]]
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y curl unzip openssl ca-certificates nftables python3{egress_packages_apt}
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y curl unzip openssl ca-certificates nftables python3{egress_packages_rpm}
elif command -v yum >/dev/null 2>&1; then
  yum install -y curl unzip openssl ca-certificates nftables python3{egress_packages_rpm}
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
{egress_setup}
mkdir -p /usr/local/etc/xray
cat >/usr/local/etc/xray/config.json <<EOF
{{
  \"log\": {{\"loglevel\": \"warning\"}},
  \"inbounds\": [
    {{
      \"tag\": \"base-vless\",
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
  \"outbounds\": {outbounds_json},
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
{egress_print}
"""
