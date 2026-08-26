#!/usr/bin/env bash
set -euo pipefail

: "${CONTROL_IP:?CONTROL_IP is required}"
: "${NODE_IP:?NODE_IP is required}"
: "${NODE_ID:?NODE_ID is required}"
[[ "$CONTROL_IP" =~ ^[0-9.]+$ ]]
[[ "$NODE_IP" =~ ^[0-9.]+$ ]]
[[ "$NODE_ID" =~ ^[1-9][0-9]*$ ]]
hostname -I | grep -qw "$CONTROL_IP"

user_name=emery-gate-tunnel
id "$user_name" >/dev/null 2>&1 || useradd --system --create-home \
  --home-dir "/var/lib/$user_name" --shell /bin/bash "$user_name"
random_password="$(openssl rand -base64 48)"
usermod -p "$(openssl passwd -6 "$random_password")" "$user_name"
unset random_password
install -d -m 700 -o "$user_name" -g "$user_name" "/var/lib/$user_name/.ssh"
touch "/var/lib/$user_name/.ssh/authorized_keys"
chown "$user_name:$user_name" "/var/lib/$user_name/.ssh/authorized_keys"
chmod 600 "/var/lib/$user_name/.ssh/authorized_keys"

gate_key="$(/opt/nt/orchestrator/.venv/bin/python -c 'from dotenv import dotenv_values; print((dotenv_values("/opt/nt/emery vpn orchestrator/.env").get("DEVICE_GATE_API_KEY") or "").strip())')"
[[ "${#gate_key}" -ge 32 ]]
printf '%s' "$gate_key" > "/var/lib/$user_name/device_gate_key"
chown "$user_name:$user_name" "/var/lib/$user_name/device_gate_key"
chmod 400 "/var/lib/$user_name/device_gate_key"
unset gate_key

cat > /usr/local/sbin/emery-tunnel-enroll.py <<'PY'
#!/usr/bin/env python3
import base64
import os
import pathlib
import socket

allowed_ip = os.environ["ALLOWED_IP"]
node_id = int(os.environ["NODE_ID"])
auth_file = pathlib.Path("/var/lib/emery-gate-tunnel/.ssh/authorized_keys")
options = (
    'restrict,port-forwarding,permitopen="127.0.0.1:8080",'
    'command="/bin/cat /var/lib/emery-gate-tunnel/device_gate_key" '
)

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("0.0.0.0", 80))
server.listen(4)
server.settimeout(300)
try:
    while True:
        connection, address = server.accept()
        with connection:
            connection.settimeout(10)
            data = connection.recv(2048)
            if address[0] != allowed_ip:
                connection.sendall(b"DENIED\n")
                continue
            try:
                parts = data.decode("ascii").strip().split()
                if len(parts) < 2 or parts[0] != "ssh-ed25519":
                    raise ValueError("wrong key type")
                decoded = base64.b64decode(parts[1], validate=True)
                if len(decoded) < 48:
                    raise ValueError("invalid key")
            except (UnicodeDecodeError, ValueError):
                connection.sendall(b"INVALID\n")
                continue
            auth_file.write_text(
                f'{options}{parts[0]} {parts[1]} emery-gate-node-{node_id}\n',
                encoding="ascii",
            )
            account = pathlib.Path("/var/lib/emery-gate-tunnel").stat()
            os.chown(auth_file, account.st_uid, account.st_gid)
            os.chmod(auth_file, 0o600)
            connection.sendall(b"OK\n")
            break
finally:
    server.close()
PY
chmod 700 /usr/local/sbin/emery-tunnel-enroll.py

if iptables -nL SKRYON_BACKEND_INPUT >/dev/null 2>&1; then
  firewall_chain=SKRYON_BACKEND_INPUT
else
  firewall_chain=INPUT
fi
iptables -C "$firewall_chain" -s "$NODE_IP" -p tcp --dport 80 -j ACCEPT 2>/dev/null || \
  iptables -I "$firewall_chain" 1 -s "$NODE_IP" -p tcp --dport 80 -j ACCEPT

cat > /etc/systemd/system/emery-gate-enroll.service <<UNIT
[Unit]
Description=One-time Emery gate SSH key enrollment
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=ALLOWED_IP=$NODE_IP
Environment=NODE_ID=$NODE_ID
ExecStart=/usr/bin/python3 /usr/local/sbin/emery-tunnel-enroll.py
ExecStopPost=/bin/sh -c '/usr/sbin/iptables -D $firewall_chain -s $NODE_IP -p tcp --dport 80 -j ACCEPT 2>/dev/null || true'
RuntimeMaxSec=300
Restart=no
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/emery-gate-tunnel/.ssh
RestrictAddressFamilies=AF_INET AF_UNIX

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl stop emery-gate-enroll.service 2>/dev/null || true
systemctl reset-failed emery-gate-enroll.service 2>/dev/null || true
systemctl start emery-gate-enroll.service
sleep 1
systemctl is-active --quiet emery-gate-enroll.service
ss -lntH 'sport = :80' | grep -q LISTEN
echo 'CONTROL_READY: switch to 82.165.163.77 and run the node command within 5 minutes'
