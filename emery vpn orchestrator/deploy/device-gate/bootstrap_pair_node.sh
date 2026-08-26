#!/usr/bin/env bash
set -euo pipefail

: "${NODE_IP:?NODE_IP is required}"
: "${CONTROL_IP:?CONTROL_IP is required}"
: "${CONTROL_SSH_PORT:?CONTROL_SSH_PORT is required}"
: "${NODE_ID:?NODE_ID is required}"
: "${GATE_HOST:?GATE_HOST is required}"
: "${GATE_PORT:?GATE_PORT is required}"
: "${GATE_SPKI_SHA256:?GATE_SPKI_SHA256 is required}"
: "${CONTROL_HOST_KEY_SHA256:?CONTROL_HOST_KEY_SHA256 is required}"

[[ "$NODE_IP" =~ ^[0-9.]+$ ]]
[[ "$CONTROL_IP" =~ ^[0-9.]+$ ]]
[[ "$CONTROL_SSH_PORT" =~ ^[0-9]+$ ]]
[[ "$NODE_ID" =~ ^[1-9][0-9]*$ ]]
[[ "$GATE_HOST" =~ ^[A-Za-z0-9.-]+$ ]]
[[ "$GATE_PORT" =~ ^[0-9]+$ ]]
[[ "$GATE_SPKI_SHA256" =~ ^[a-f0-9]{64}$ ]]
hostname -I | grep -qw "$NODE_IP"

install -d -m 700 /etc/emery
test -s /etc/emery/gate_tunnel_ed25519 || \
  ssh-keygen -q -t ed25519 -N '' -f /etc/emery/gate_tunnel_ed25519

enroll_result="$(python3 - "$CONTROL_IP" /etc/emery/gate_tunnel_ed25519.pub <<'PY'
import pathlib
import socket
import sys

host = sys.argv[1]
public_key = pathlib.Path(sys.argv[2]).read_bytes().strip() + b"\n"
with socket.create_connection((host, 80), timeout=12) as connection:
    connection.sendall(public_key)
    connection.shutdown(socket.SHUT_WR)
    print(connection.recv(128).decode("ascii").strip())
PY
)"
[[ "$enroll_result" == OK ]]

ssh-keyscan -T 8 -p "$CONTROL_SSH_PORT" "$CONTROL_IP" > /etc/emery/bootstrap_known_hosts 2>/dev/null
ssh-keygen -lf /etc/emery/bootstrap_known_hosts | grep -Fq "$CONTROL_HOST_KEY_SHA256"
gate_key="$(ssh -T -p "$CONTROL_SSH_PORT" \
  -i /etc/emery/gate_tunnel_ed25519 \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/etc/emery/bootstrap_known_hosts \
  emery-gate-tunnel@"$CONTROL_IP" 2>/dev/null)"
[[ "${#gate_key}" -ge 32 ]]

bootstrap_url='https://raw.githubusercontent.com/mafimusic5-beep/nt/90b41601cc0ce08c7e48990ba4a4d3290d31fce9/emery%20vpn%20orchestrator/deploy/device-gate/bootstrap_node.sh'
bootstrap_sha256='7705ed7f0ecfd8ebf22ea9b4f70fdfc0a2f4652cbd563d85d6ee9d09d88b1ba7'
curl -fsSL --retry 3 "$bootstrap_url" -o /tmp/bootstrap_node.sh
echo "$bootstrap_sha256  /tmp/bootstrap_node.sh" | sha256sum -c -

export node_ip="$NODE_IP"
export control_ip="$CONTROL_IP"
export control_ssh_port="$CONTROL_SSH_PORT"
export node_id="$NODE_ID"
export gate_host="$GATE_HOST"
export gate_port="$GATE_PORT"
export gate_spki_sha256="$GATE_SPKI_SHA256"
export control_host_key_sha256="$CONTROL_HOST_KEY_SHA256"
export gate_key_base64="$(printf '%s' "$gate_key" | base64 -w0)"
export gateway_script_url='https://raw.githubusercontent.com/mafimusic5-beep/nt/85caa7eb9a47c8c5efd0263c526ce38945dc06b5/emery%20vpn%20orchestrator/deploy/device-gate/emery_device_gate.py'
export gateway_script_sha256='a5d1e1c1ef1bd7b962cc29f4afc314ed376b15d87a6429d6d95439b1605ae2aa'
unset gate_key

bash /tmp/bootstrap_node.sh
rm -f /tmp/bootstrap_node.sh /etc/emery/bootstrap_known_hosts
unset gate_key_base64

if timeout 8 bash -c "</dev/tcp/$NODE_IP/$GATE_PORT" 2>/dev/null; then
  echo 'PUBLIC_GATE_REACHABLE'
else
  echo "GATE_LOCAL_READY: if the app cannot connect, allow TCP $GATE_PORT in the IONOS firewall"
fi
