from __future__ import annotations

import base64
import binascii
import ipaddress
import logging
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from src.common.config import settings

logger = logging.getLogger(__name__)

_IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


@dataclass(frozen=True, slots=True)
class ManualIspEgressPlan:
    interface: str
    client_ip: str
    client_cidr: str
    gateway_ip: str
    tunnel_cidr: str
    peer_endpoint: str
    peer_public_key: str
    mtu: int
    keepalive_seconds: int
    routing_table: int


class ManualIspEgressService:
    """Register one manual VPS as a unique WireGuard peer of an ISP egress.

    The ISP egress itself is a separately controlled Linux host with a persistent
    wg-quick interface. New VPN nodes get unique WireGuard keys and tunnel IPs;
    this service adds each public key to the egress and persists NAT/forwarding.
    """

    @staticmethod
    def _valid_wireguard_key(value: str) -> bool:
        try:
            decoded = base64.b64decode(value.strip(), validate=True)
        except (ValueError, binascii.Error):
            return False
        return len(decoded) == 32

    @staticmethod
    def _peer_endpoint(host: str, port: int) -> str:
        safe_host = host.strip()
        if not safe_host or any(char.isspace() for char in safe_host):
            raise ValueError("isp_egress_endpoint_invalid")
        if port < 1 or port > 65535:
            raise ValueError("isp_egress_port_invalid")
        try:
            parsed = ipaddress.ip_address(safe_host.strip("[]"))
        except ValueError:
            if "/" in safe_host or safe_host.startswith(".") or safe_host.endswith("."):
                raise ValueError("isp_egress_endpoint_invalid") from None
            return f"{safe_host}:{port}"
        if parsed.version == 6:
            return f"[{parsed.compressed}]:{port}"
        return f"{parsed.compressed}:{port}"

    def plan_for_node(self, node_id: int) -> ManualIspEgressPlan | None:
        if not settings.manual_bootstrap_isp_egress_enabled:
            return None
        if node_id < 1:
            raise ValueError("isp_egress_node_id_invalid")

        interface = settings.manual_bootstrap_isp_egress_interface.strip()
        if not _IFACE_RE.fullmatch(interface):
            raise ValueError("isp_egress_interface_invalid")

        peer_public_key = settings.manual_bootstrap_isp_egress_public_key.strip()
        if not self._valid_wireguard_key(peer_public_key):
            raise ValueError("isp_egress_public_key_invalid")

        try:
            network = ipaddress.ip_network(
                settings.manual_bootstrap_isp_egress_cidr.strip(),
                strict=False,
            )
        except ValueError:
            raise ValueError("isp_egress_cidr_invalid") from None
        if network.version != 4 or network.prefixlen > 30 or not network.is_private:
            raise ValueError("isp_egress_cidr_invalid")

        # First usable address belongs to the ISP egress. Node #1 gets the next
        # address, node #2 the following one, etc. A /16 leaves ample room while
        # making allocation deterministic without storing another secret/state.
        gateway_ip = network.network_address + 1
        client_ip = network.network_address + node_id + 1
        if client_ip >= network.broadcast_address:
            raise ValueError("isp_egress_address_pool_exhausted")

        mtu = int(settings.manual_bootstrap_isp_egress_mtu)
        if mtu < 1280 or mtu > 1420:
            raise ValueError("isp_egress_mtu_invalid")
        keepalive = int(settings.manual_bootstrap_isp_egress_keepalive_seconds)
        if keepalive < 0 or keepalive > 120:
            raise ValueError("isp_egress_keepalive_invalid")
        routing_table = int(settings.manual_bootstrap_isp_egress_routing_table)
        if routing_table < 1 or routing_table > 2_147_483_647:
            raise ValueError("isp_egress_routing_table_invalid")

        return ManualIspEgressPlan(
            interface=interface,
            client_ip=str(client_ip),
            client_cidr=f"{client_ip}/32",
            gateway_ip=str(gateway_ip),
            tunnel_cidr=str(network),
            peer_endpoint=self._peer_endpoint(
                settings.manual_bootstrap_isp_egress_host,
                int(settings.manual_bootstrap_isp_egress_port),
            ),
            peer_public_key=peer_public_key,
            mtu=mtu,
            keepalive_seconds=keepalive,
            routing_table=routing_table,
        )

    def register_peer(self, plan: ManualIspEgressPlan, client_public_key: str) -> dict:
        if not self._valid_wireguard_key(client_public_key):
            return {"ok": False, "detail": "isp_egress_client_public_key_invalid"}

        ssh_host = (
            settings.manual_bootstrap_isp_egress_ssh_host.strip()
            or settings.manual_bootstrap_isp_egress_host.strip()
        )
        ssh_user = settings.manual_bootstrap_isp_egress_ssh_user.strip()
        key_path = settings.manual_bootstrap_isp_egress_ssh_private_key_path.strip()
        if not ssh_host or not ssh_user or not key_path:
            return {"ok": False, "detail": "isp_egress_ssh_not_configured"}
        if not Path(key_path).is_file():
            return {"ok": False, "detail": "isp_egress_ssh_key_missing"}

        try:
            import paramiko  # type: ignore
        except ImportError:
            return {"ok": False, "detail": "paramiko_not_installed"}

        client = paramiko.SSHClient()
        client.load_system_host_keys()
        known_hosts = settings.manual_bootstrap_isp_egress_ssh_known_hosts_path.strip()
        if known_hosts:
            try:
                client.load_host_keys(known_hosts)
            except OSError:
                return {"ok": False, "detail": "isp_egress_known_hosts_missing"}
        if settings.manual_bootstrap_isp_egress_allow_unknown_host_keys:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())

        timeout = max(int(settings.manual_bootstrap_isp_egress_ssh_timeout_seconds), 5)
        try:
            client.connect(
                hostname=ssh_host,
                port=int(settings.manual_bootstrap_isp_egress_ssh_port),
                username=ssh_user,
                key_filename=key_path,
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            stdin, stdout, stderr = client.exec_command("bash -s", timeout=120)
            stdin.write(self._registration_script(plan, client_public_key))
            stdin.flush()
            stdin.channel.shutdown_write()
            out = stdout.read().decode(errors="ignore").strip()
            err = stderr.read().decode(errors="ignore").strip()
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                logger.warning(
                    "ISP egress peer registration failed host=%s rc=%s err=%s",
                    ssh_host,
                    rc,
                    err[:200],
                )
                return {"ok": False, "detail": "isp_egress_peer_registration_failed"}
            if "SKRYON_EGRESS_PEER_OK" not in out:
                return {"ok": False, "detail": "isp_egress_peer_registration_unverified"}
            return {"ok": True, "detail": "isp_egress_peer_registered"}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ISP egress SSH failed host=%s error=%s",
                ssh_host,
                type(exc).__name__,
            )
            return {"ok": False, "detail": f"isp_egress_ssh_failed:{type(exc).__name__}"}
        finally:
            client.close()

    @staticmethod
    def _registration_script(plan: ManualIspEgressPlan, client_public_key: str) -> str:
        iface = shlex.quote(plan.interface)
        client_key = shlex.quote(client_public_key.strip())
        client_ip = shlex.quote(plan.client_ip)
        tunnel_cidr = shlex.quote(plan.tunnel_cidr)
        return f"""#!/usr/bin/env bash
set -euo pipefail
[[ \"$(id -u)\" -eq 0 ]]
IFACE={iface}
CLIENT_KEY={client_key}
CLIENT_IP={client_ip}
TUNNEL_CIDR={tunnel_cidr}

if ! command -v wg >/dev/null 2>&1 || ! command -v nft >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y wireguard-tools nftables
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y wireguard-tools nftables
  elif command -v yum >/dev/null 2>&1; then
    yum install -y wireguard-tools nftables
  fi
fi
command -v wg >/dev/null 2>&1
command -v nft >/dev/null 2>&1
wg show \"$IFACE\" >/dev/null
[[ -f \"/etc/wireguard/$IFACE.conf\" ]]

printf 'net.ipv4.ip_forward=1\n' >/etc/sysctl.d/99-skryon-isp-egress.conf
sysctl -w net.ipv4.ip_forward=1 >/dev/null

cat >/usr/local/sbin/skryon-isp-egress-nat <<EOF
#!/usr/bin/env bash
set -euo pipefail
TUNNEL_CIDR='$TUNNEL_CIDR'
WG_IFACE='$IFACE'
WAN_IF=\"\\$(ip -4 route show default | awk 'NR==1 {{for (i=1;i<=NF;i++) if (\\$i==\"dev\") {{print \\$(i+1); exit}}}}')\"
[[ -n \"\\$WAN_IF\" ]]
nft delete table ip skryon_isp_egress_nat >/dev/null 2>&1 || true
nft -f - <<NFT
 table ip skryon_isp_egress_nat {{
   chain postrouting {{
     type nat hook postrouting priority srcnat; policy accept;
     ip saddr \\$TUNNEL_CIDR oifname \"\\$WAN_IF\" masquerade
   }}
 }}
NFT
EOF
chmod 0755 /usr/local/sbin/skryon-isp-egress-nat
/usr/local/sbin/skryon-isp-egress-nat

cat >/etc/systemd/system/skryon-isp-egress-nat.service <<EOF
[Unit]
Description=Skryon ISP egress NAT
After=network-online.target wg-quick@$IFACE.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/skryon-isp-egress-nat

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable skryon-isp-egress-nat.service >/dev/null 2>&1 || true

wg set \"$IFACE\" peer \"$CLIENT_KEY\" allowed-ips \"$CLIENT_IP/32\"
wg-quick save \"$IFACE\" >/dev/null
printf 'SKRYON_EGRESS_PEER_OK\n'
"""
