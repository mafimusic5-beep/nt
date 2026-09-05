from __future__ import annotations

import base64
import ipaddress
import json
import logging
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

from src.backend.services.node_recovery_service import SshAndProviderRecoveryTransport
from src.common.config import settings
from src.common.models import VpnNode

logger = logging.getLogger(__name__)


class ManualDeviceGateService:
    """Install a self-renewing device gate on a manually supplied VPS.

    The public gate uses a short-lived Let's Encrypt certificate issued directly
    for the VPS IPv4 address. This deliberately avoids per-node DNS automation:
    /setup_server only needs the VPS IP/password. The authorize request travels
    over the already-public Skryon HTTPS API and still requires the separate
    DEVICE_GATE_API_KEY plus the Android Keystore proof.
    """

    _PUBLIC_AUTHORIZE_PATH = "/api/device-gate/authorize"
    _SPKI_RE = re.compile(r"^[a-f0-9]{64}$")

    def __init__(self) -> None:
        self.ssh = SshAndProviderRecoveryTransport()

    @staticmethod
    def _gateway_source() -> str:
        root = Path(__file__).resolve().parents[3]
        source_path = root / "deploy" / "device-gate" / "emery_device_gate.py"
        source = source_path.read_text(encoding="utf-8")
        old_check = 'if parsed.path != "/internal/device-gate/authorize":'
        new_check = (
            'if parsed.path not in {'
            '"/internal/device-gate/authorize", "/api/device-gate/authorize"'
            '}:'
        )
        if old_check in source:
            source = source.replace(old_check, new_check, 1)
        elif new_check not in source:
            raise RuntimeError("device_gate_authorize_path_guard_missing")
        return source

    @classmethod
    def _validated_authorize_url(cls) -> str:
        value = (settings.manual_bootstrap_device_gate_authorize_url or "").strip()
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError("device_gate_authorize_url_invalid") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path != cls._PUBLIC_AUTHORIZE_PATH
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("device_gate_authorize_url_invalid")
        return value

    @staticmethod
    def _validated_ipv4(endpoint: str) -> str:
        try:
            address = ipaddress.ip_address(endpoint.strip())
        except ValueError as exc:
            raise ValueError("manual_device_gate_requires_ipv4") from exc
        if address.version != 4:
            raise ValueError("manual_device_gate_requires_ipv4")
        return str(address)

    @classmethod
    def _remote_script(
        cls,
        *,
        node_id: int,
        node_ip: str,
        gate_port: int,
        authorize_url: str,
        authorize_key: str,
    ) -> str:
        source_b64 = base64.b64encode(cls._gateway_source().encode("utf-8")).decode("ascii")
        key_b64 = base64.b64encode(authorize_key.encode("utf-8")).decode("ascii")
        q_ip = shlex.quote(node_ip)
        q_url = shlex.quote(authorize_url)
        q_source = shlex.quote(source_b64)
        q_key = shlex.quote(key_b64)
        port = int(gate_port)
        node = int(node_id)
        return f"""#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$(id -u)" -eq 0 ]]
NODE_IP={q_ip}
NODE_ID={node}
GATE_PORT={port}
AUTHORIZE_URL={q_url}
GATE_SOURCE_B64={q_source}
GATE_KEY_B64={q_key}
CERTBOT_ROOT=/opt/emery/certbot
CERTBOT="$CERTBOT_ROOT/bin/certbot"
CERT_DIR="/etc/letsencrypt/live/$NODE_IP"
CERT_FILE="$CERT_DIR/fullchain.pem"
KEY_FILE="$CERT_DIR/privkey.pem"
ACME_COMMENT=EMERY_ACME_HTTP01

case "$NODE_IP" in
  *[!0-9.]*|'') echo 'invalid node ip' >&2; exit 64 ;;
esac
[[ "$NODE_ID" =~ ^[1-9][0-9]*$ ]]
[[ "$GATE_PORT" =~ ^[0-9]+$ ]]

export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip openssl ca-certificates acl iptables
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-pip openssl ca-certificates acl iptables
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 python3-pip openssl ca-certificates acl iptables
else
  echo 'unsupported package manager' >&2
  exit 65
fi

install -d -m 0755 /opt/emery
if [[ ! -x "$CERTBOT_ROOT/bin/python" ]]; then
  python3 -m venv "$CERTBOT_ROOT"
fi
"$CERTBOT_ROOT/bin/python" -m pip install --quiet --upgrade pip
"$CERTBOT_ROOT/bin/python" -m pip install --quiet --upgrade 'certbot>=5.4,<6'
"$CERTBOT" --help all | grep -q -- '--ip-address'
"$CERTBOT" --help all | grep -q -- '--preferred-profile'

acme_open() {{
  iptables -C INPUT -p tcp --dport 80 -m comment --comment "$ACME_COMMENT" -j ACCEPT 2>/dev/null || \
    iptables -I INPUT 1 -p tcp --dport 80 -m comment --comment "$ACME_COMMENT" -j ACCEPT
}}
acme_close() {{
  iptables -D INPUT -p tcp --dport 80 -m comment --comment "$ACME_COMMENT" -j ACCEPT 2>/dev/null || true
}}
trap acme_close EXIT

if [[ ! -s "$CERT_FILE" || ! -s "$KEY_FILE" ]] || ! openssl x509 -checkend 172800 -noout -in "$CERT_FILE" >/dev/null 2>&1; then
  acme_open
  "$CERTBOT" certonly \
    --non-interactive \
    --agree-tos \
    --register-unsafely-without-email \
    --preferred-profile shortlived \
    --standalone \
    --reuse-key \
    --ip-address "$NODE_IP" \
    --cert-name "$NODE_IP"
  acme_close
fi
[[ -s "$CERT_FILE" && -s "$KEY_FILE" ]]

GATE_SPKI="$(
  openssl x509 -in "$CERT_FILE" -pubkey -noout \
    | openssl pkey -pubin -outform DER 2>/dev/null \
    | openssl dgst -sha256 \
    | awk '{{print $NF}}'
)"
[[ "$GATE_SPKI" =~ ^[a-f0-9]{{64}}$ ]]

getent group emery-gate >/dev/null || groupadd --system emery-gate
id emery-gate >/dev/null 2>&1 || useradd --system --create-home \
  --home-dir /var/lib/emery-gate --gid emery-gate --shell /usr/sbin/nologin emery-gate
install -d -m 0750 -o root -g emery-gate /etc/emery
install -d -m 0755 /opt/emery/device-gate
printf '%s' "$GATE_SOURCE_B64" | base64 -d > /opt/emery/device-gate/emery_device_gate.py.new
python3 -m py_compile /opt/emery/device-gate/emery_device_gate.py.new
install -m 0755 /opt/emery/device-gate/emery_device_gate.py.new /opt/emery/device-gate/emery_device_gate.py
rm -f /opt/emery/device-gate/emery_device_gate.py.new

apply_cert_acl() {{
  for directory in /etc/letsencrypt /etc/letsencrypt/live /etc/letsencrypt/archive "$CERT_DIR"; do
    [[ -d "$directory" ]] && setfacl -m u:emery-gate:rx "$directory"
  done
  archive_dir="$(dirname "$(readlink -f "$CERT_FILE")")"
  [[ -d "$archive_dir" ]] && setfacl -m u:emery-gate:rx "$archive_dir"
  setfacl -m u:emery-gate:r "$(readlink -f "$CERT_FILE")" "$(readlink -f "$KEY_FILE")"
}}
apply_cert_acl

GATE_KEY="$(printf '%s' "$GATE_KEY_B64" | base64 -d)"
[[ "${{#GATE_KEY}}" -ge 32 ]]
umask 077
cat > /etc/emery/device-gate.env <<EOF
EMERY_GATE_BIND_HOST=0.0.0.0
EMERY_GATE_BIND_PORT=$GATE_PORT
EMERY_GATE_NODE_ID=$NODE_ID
EMERY_GATE_SERVER_NAME=$NODE_IP
EMERY_GATE_SPKI_SHA256=$GATE_SPKI
EMERY_GATE_TLS_CERT_FILE=$CERT_FILE
EMERY_GATE_TLS_KEY_FILE=$KEY_FILE
EMERY_GATE_AUTHORIZE_URL=$AUTHORIZE_URL
EMERY_GATE_AUTHORIZE_KEY=$GATE_KEY
EMERY_GATE_CONTROL_TIMEOUT_SECONDS=10
EMERY_GATE_CONNECT_TIMEOUT_SECONDS=5
EMERY_GATE_MAX_CONNECTIONS=2048
EMERY_GATE_LOG_LEVEL=INFO
EOF
chmod 0600 /etc/emery/device-gate.env
unset GATE_KEY GATE_KEY_B64

cat > /etc/systemd/system/emery-gate-firewall.service <<EOF
[Unit]
Description=Firewall opening for Skryon device gate
Before=emery-device-gate.service

[Service]
Type=oneshot
ExecStart=/bin/sh -c '/usr/sbin/iptables -C INPUT -p tcp --dport $GATE_PORT -m comment --comment EMERY_DEVICE_GATE -j ACCEPT 2>/dev/null || /usr/sbin/iptables -I INPUT 1 -p tcp --dport $GATE_PORT -m comment --comment EMERY_DEVICE_GATE -j ACCEPT'
ExecStop=/bin/sh -c '/usr/sbin/iptables -D INPUT -p tcp --dport $GATE_PORT -m comment --comment EMERY_DEVICE_GATE -j ACCEPT 2>/dev/null || true'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/emery-device-gate.service <<'EOF'
[Unit]
Description=Skryon device-bound VLESS gateway
After=network-online.target emery-gate-firewall.service
Wants=network-online.target
Requires=emery-gate-firewall.service

[Service]
Type=simple
User=emery-gate
Group=emery-gate
EnvironmentFile=/etc/emery/device-gate.env
ExecStart=/usr/bin/python3 /opt/emery/device-gate/emery_device_gate.py
Restart=always
RestartSec=2
UMask=0077
LimitNOFILE=65536
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/sbin/emery-gate-cert-deploy <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
CERT_FILE='$CERT_FILE'
KEY_FILE='$KEY_FILE'
CERT_DIR='$CERT_DIR'
for directory in /etc/letsencrypt /etc/letsencrypt/live /etc/letsencrypt/archive "\$CERT_DIR"; do
  [[ -d "\$directory" ]] && setfacl -m u:emery-gate:rx "\$directory"
done
archive_dir="\$(dirname "\$(readlink -f "\$CERT_FILE")")"
[[ -d "\$archive_dir" ]] && setfacl -m u:emery-gate:rx "\$archive_dir"
setfacl -m u:emery-gate:r "\$(readlink -f "\$CERT_FILE")" "\$(readlink -f "\$KEY_FILE")"
systemctl try-restart emery-device-gate.service >/dev/null 2>&1 || true
EOF
chmod 0755 /usr/local/sbin/emery-gate-cert-deploy

cat > /usr/local/sbin/emery-gate-cert-renew <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
COMMENT='$ACME_COMMENT'
cleanup() {{
  iptables -D INPUT -p tcp --dport 80 -m comment --comment "\$COMMENT" -j ACCEPT 2>/dev/null || true
}}
trap cleanup EXIT
iptables -C INPUT -p tcp --dport 80 -m comment --comment "\$COMMENT" -j ACCEPT 2>/dev/null || \
  iptables -I INPUT 1 -p tcp --dport 80 -m comment --comment "\$COMMENT" -j ACCEPT
'$CERTBOT' renew --non-interactive --preferred-profile shortlived --reuse-key \
  --deploy-hook /usr/local/sbin/emery-gate-cert-deploy
EOF
chmod 0755 /usr/local/sbin/emery-gate-cert-renew

cat > /etc/systemd/system/emery-gate-cert-renew.service <<'EOF'
[Unit]
Description=Renew short-lived Skryon IP certificate
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/emery-gate-cert-renew
EOF

cat > /etc/systemd/system/emery-gate-cert-renew.timer <<'EOF'
[Unit]
Description=Schedule Skryon IP certificate renewal

[Timer]
OnBootSec=1h
OnUnitActiveSec=12h
RandomizedDelaySec=30m
Persistent=true
Unit=emery-gate-cert-renew.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now emery-gate-firewall.service emery-device-gate.service emery-gate-cert-renew.timer >/dev/null
systemctl restart emery-gate-firewall.service emery-device-gate.service
sleep 2
systemctl is-active --quiet emery-gate-firewall.service
systemctl is-active --quiet emery-device-gate.service
systemctl is-active --quiet emery-gate-cert-renew.timer
ss -lntH "sport = :$GATE_PORT" | grep -q LISTEN

AUTH_STATUS="$(curl -sS -o /dev/null -w '%{{http_code}}' --max-time 12 \
  -H 'Content-Type: application/json' -d '{{}}' "$AUTHORIZE_URL" || true)"
[[ "$AUTH_STATUS" == 422 ]]

timeout 10 openssl s_client -connect "127.0.0.1:$GATE_PORT" -verify_ip "$NODE_IP" \
  -verify_return_error -CApath /etc/ssl/certs </dev/null >/tmp/emery-gate-tls-check.log 2>&1 || {{
    cat /tmp/emery-gate-tls-check.log >&2
    rm -f /tmp/emery-gate-tls-check.log
    exit 66
  }}
rm -f /tmp/emery-gate-tls-check.log

printf 'GATE_HOST=%s\n' "$NODE_IP"
printf 'GATE_PORT=%s\n' "$GATE_PORT"
printf 'GATE_SERVER_NAME=%s\n' "$NODE_IP"
printf 'GATE_SPKI=%s\n' "$GATE_SPKI"
"""

    def bootstrap(self, node: VpnNode) -> dict[str, object]:
        endpoint = (node.endpoint or "").strip()
        try:
            node_ip = self._validated_ipv4(endpoint)
            authorize_url = self._validated_authorize_url()
        except ValueError as exc:
            return {"status": "failed", "detail": str(exc)}

        gate_key = (settings.device_gate_api_key or "").strip()
        if len(gate_key) < 32:
            return {"status": "failed", "detail": "device_gate_api_key_missing"}
        gate_port = int(settings.manual_bootstrap_device_gate_port)
        if gate_port < 1 or gate_port > 65535:
            return {"status": "failed", "detail": "device_gate_port_invalid"}
        if not (node.ssh_private_key or "").strip() or not (node.ssh_host_key or "").strip():
            return {"status": "failed", "detail": "device_gate_ssh_identity_not_pinned"}

        script = self._remote_script(
            node_id=int(node.id),
            node_ip=node_ip,
            gate_port=gate_port,
            authorize_url=authorize_url,
            authorize_key=gate_key,
        )
        client = None
        try:
            client = self.ssh._connect(node)
            stdin, stdout, stderr = client.exec_command("bash -s", timeout=240)
            stdin.write(script)
            stdin.flush()
            stdin.channel.shutdown_write()
            out = stdout.read().decode(errors="ignore").strip()
            err = stderr.read().decode(errors="ignore").strip()
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                safe_error = (err or out or f"exit_{rc}").replace("\n", " ")[:300]
                return {
                    "status": "failed",
                    "detail": f"device_gate_bootstrap_failed:{safe_error}",
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "manual device gate bootstrap failed: node_id=%s endpoint=%s error=%s",
                node.id,
                endpoint,
                type(exc).__name__,
            )
            return {
                "status": "failed",
                "detail": f"device_gate_ssh_failed:{type(exc).__name__}",
            }
        finally:
            if client is not None:
                client.close()

        values: dict[str, str] = {}
        for line in out.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"GATE_HOST", "GATE_PORT", "GATE_SERVER_NAME", "GATE_SPKI"}:
                values[key] = value.strip()
        if (
            values.get("GATE_HOST") != node_ip
            or values.get("GATE_SERVER_NAME") != node_ip
            or values.get("GATE_PORT") != str(gate_port)
            or not self._SPKI_RE.fullmatch(values.get("GATE_SPKI", ""))
        ):
            return {"status": "failed", "detail": "device_gate_bootstrap_values_invalid"}

        return {
            "status": "ok",
            "host": node_ip,
            "port": gate_port,
            "server_name": node_ip,
            "spki_sha256": values["GATE_SPKI"],
        }
