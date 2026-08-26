#!/usr/bin/env bash
set -euo pipefail

: "${control_ip:?control_ip is required}"
: "${tunnel_public_key_base64:?tunnel_public_key_base64 is required}"
: "${node_id:?node_id is required}"

[[ "$control_ip" =~ ^[0-9.]+$ ]]
[[ "$node_id" =~ ^[1-9][0-9]*$ ]]
hostname -I | grep -qw "$control_ip"

tunnel_public_key="$(printf '%s' "$tunnel_public_key_base64" | base64 -d)"
set -- $tunnel_public_key
[[ "${1:-}" == ssh-ed25519 ]]
[[ -n "${2:-}" ]]
public_key="$1 $2"
unset tunnel_public_key tunnel_public_key_base64

user_name=emery-gate-tunnel
id "$user_name" >/dev/null 2>&1 || useradd --system --create-home \
  --home-dir "/var/lib/$user_name" --shell /bin/bash "$user_name"

random_password="$(openssl rand -base64 48)"
usermod -p "$(openssl passwd -6 "$random_password")" "$user_name"
unset random_password

install -d -m 700 -o "$user_name" -g "$user_name" "/var/lib/$user_name/.ssh"
printf '%s\n' \
  "restrict,port-forwarding,permitopen=\"127.0.0.1:8080\",command=\"/bin/cat /var/lib/$user_name/device_gate_key\" $public_key emery-gate-node-$node_id" \
  > "/var/lib/$user_name/.ssh/authorized_keys"
chown "$user_name:$user_name" "/var/lib/$user_name/.ssh/authorized_keys"
chmod 600 "/var/lib/$user_name/.ssh/authorized_keys"

gate_key="$(/opt/nt/orchestrator/.venv/bin/python -c 'from dotenv import dotenv_values; print((dotenv_values("/opt/nt/emery vpn orchestrator/.env").get("DEVICE_GATE_API_KEY") or "").strip())')"
[[ "${#gate_key}" -ge 32 ]]
printf '%s' "$gate_key" > "/var/lib/$user_name/device_gate_key"
chown "$user_name:$user_name" "/var/lib/$user_name/device_gate_key"
chmod 400 "/var/lib/$user_name/device_gate_key"
unset gate_key public_key

echo 'CONTROL_READY'
