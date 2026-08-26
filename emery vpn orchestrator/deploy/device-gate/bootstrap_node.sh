#!/usr/bin/env bash
set -euo pipefail

for required_name in node_ip control_ip control_ssh_port node_id gate_host gate_port gate_spki_sha256 control_host_key_sha256 gate_key_base64 gateway_script_url gateway_script_sha256; do
  [[ -n "${!required_name:-}" ]] || { echo "Missing $required_name" >&2; exit 64; }
done

[[ "$node_ip" =~ ^[0-9.]+$ ]]
[[ "$control_ip" =~ ^[0-9.]+$ ]]
[[ "$control_ssh_port" =~ ^[0-9]+$ ]]
[[ "$node_id" =~ ^[1-9][0-9]*$ ]]
[[ "$gate_host" =~ ^[A-Za-z0-9.-]+$ ]]
[[ "$gate_port" =~ ^[0-9]+$ ]]
[[ "$gate_spki_sha256" =~ ^[a-f0-9]{64}$ ]]
[[ "$control_host_key_sha256" =~ ^SHA256:[A-Za-z0-9+/]+$ ]]
[[ "$gateway_script_sha256" =~ ^[a-f0-9]{64}$ ]]
hostname -I | grep -qw "$node_ip"

gate_key="$(printf '%s' "$gate_key_base64" | base64 -d)"
[[ "${#gate_key}" -ge 32 ]]
unset gate_key_base64

cert_dir="/etc/letsencrypt/live/$gate_host"
cert_file="$cert_dir/fullchain.pem"
key_file="$cert_dir/privkey.pem"
test -s "$cert_file"
test -s "$key_file"

backup="/root/emery-gate-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
for existing in /etc/emery/device-gate.env /etc/systemd/system/emery-control-tunnel.service /etc/systemd/system/emery-gate-firewall.service /etc/systemd/system/emery-device-gate.service; do
  [[ -e "$existing" ]] && cp -a "$existing" "$backup/"
done

getent group emery-gate >/dev/null || groupadd --system emery-gate
id emery-gate >/dev/null 2>&1 || useradd --system --create-home \
  --home-dir /var/lib/emery-gate --gid emery-gate --shell /usr/sbin/nologin emery-gate
install -d -m 750 -o root -g emery-gate /etc/emery
test -s /etc/emery/gate_tunnel_ed25519 || \
  ssh-keygen -q -t ed25519 -N '' -f /etc/emery/gate_tunnel_ed25519

ssh-keyscan -T 8 -p "$control_ssh_port" "$control_ip" > /etc/emery/control_known_hosts.new 2>/dev/null
ssh-keygen -lf /etc/emery/control_known_hosts.new | grep -Fq "$control_host_key_sha256"
install -m 644 -o emery-gate -g emery-gate \
  /etc/emery/control_known_hosts.new /etc/emery/control_known_hosts
rm -f /etc/emery/control_known_hosts.new
chown emery-gate:emery-gate /etc/emery/gate_tunnel_ed25519 /etc/emery/gate_tunnel_ed25519.pub
chmod 600 /etc/emery/gate_tunnel_ed25519
chmod 644 /etc/emery/gate_tunnel_ed25519.pub

install -d -m 755 /opt/emery/device-gate
curl -fsSL --retry 3 "$gateway_script_url" -o /opt/emery/device-gate/emery_device_gate.py.new
echo "$gateway_script_sha256  /opt/emery/device-gate/emery_device_gate.py.new" | sha256sum -c -
install -m 755 /opt/emery/device-gate/emery_device_gate.py.new /opt/emery/device-gate/emery_device_gate.py
rm -f /opt/emery/device-gate/emery_device_gate.py.new

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq acl
for directory in /etc/letsencrypt /etc/letsencrypt/live /etc/letsencrypt/archive "$cert_dir" "/etc/letsencrypt/archive/$gate_host"; do
  setfacl -m u:emery-gate:rx "$directory"
done
setfacl -m u:emery-gate:r "$(readlink -f "$cert_file")" "$(readlink -f "$key_file")"

install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/20-emery-device-gate <<HOOK
#!/bin/sh
set -eu
cert=$cert_file
key=$key_file
setfacl -m u:emery-gate:r "\$(readlink -f "\$cert")" "\$(readlink -f "\$key")"
systemctl try-restart emery-device-gate.service >/dev/null 2>&1 || true
HOOK
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/20-emery-device-gate

umask 077
cat > /etc/emery/device-gate.env <<EOF
EMERY_GATE_BIND_HOST=0.0.0.0
EMERY_GATE_BIND_PORT=$gate_port
EMERY_GATE_NODE_ID=$node_id
EMERY_GATE_SERVER_NAME=$gate_host
EMERY_GATE_SPKI_SHA256=$gate_spki_sha256
EMERY_GATE_TLS_CERT_FILE=$cert_file
EMERY_GATE_TLS_KEY_FILE=$key_file
EMERY_GATE_AUTHORIZE_URL=http://127.0.0.1:18081/internal/device-gate/authorize
EMERY_GATE_AUTHORIZE_KEY=$gate_key
EMERY_GATE_CONTROL_TIMEOUT_SECONDS=10
EMERY_GATE_CONNECT_TIMEOUT_SECONDS=5
EMERY_GATE_MAX_CONNECTIONS=2048
EMERY_GATE_LOG_LEVEL=INFO
EOF
chmod 600 /etc/emery/device-gate.env
unset gate_key

cat > /etc/systemd/system/emery-control-tunnel.service <<UNIT
[Unit]
Description=Private tunnel to Skryon control API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=emery-gate
Group=emery-gate
ExecStart=/usr/bin/ssh -N -T -p $control_ssh_port -i /etc/emery/gate_tunnel_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/emery/control_known_hosts -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:18081:127.0.0.1:8080 emery-gate-tunnel@$control_ip
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/emery-gate-firewall.service <<UNIT
[Unit]
Description=Firewall opening for Emery device gate
Before=emery-device-gate.service

[Service]
Type=oneshot
ExecStart=/bin/sh -c '/usr/sbin/iptables -C INPUT -p tcp --dport $gate_port -m comment --comment EMERY_DEVICE_GATE -j ACCEPT 2>/dev/null || /usr/sbin/iptables -I INPUT 1 -p tcp --dport $gate_port -m comment --comment EMERY_DEVICE_GATE -j ACCEPT'
ExecStop=/bin/sh -c '/usr/sbin/iptables -D INPUT -p tcp --dport $gate_port -m comment --comment EMERY_DEVICE_GATE -j ACCEPT 2>/dev/null || true'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/emery-device-gate.service <<'UNIT'
[Unit]
Description=Skryon device-bound VLESS gateway
After=network-online.target emery-control-tunnel.service emery-gate-firewall.service
Wants=network-online.target
Requires=emery-control-tunnel.service emery-gate-firewall.service

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
UNIT

systemctl daemon-reload
systemctl enable emery-control-tunnel.service emery-gate-firewall.service emery-device-gate.service
systemctl restart emery-control-tunnel.service
for attempt in $(seq 1 15); do
  curl -fsS --max-time 2 http://127.0.0.1:18081/health >/dev/null && break
  sleep 1
done
curl -fsS --max-time 3 http://127.0.0.1:18081/health >/dev/null
systemctl restart emery-gate-firewall.service emery-device-gate.service
sleep 2
systemctl is-active --quiet emery-control-tunnel.service
systemctl is-active --quiet emery-gate-firewall.service
systemctl is-active --quiet emery-device-gate.service
ss -lntH "sport = :$gate_port" | grep -q LISTEN

echo "NODE_READY: tunnel + gate $gate_port active; Xray unchanged; protection still off; backup=$backup"
