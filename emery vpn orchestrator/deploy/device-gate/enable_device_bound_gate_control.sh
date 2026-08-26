#!/usr/bin/env bash
set -Eeuo pipefail

: "${CONTROL_IP:?CONTROL_IP is required}"
[[ "$CONTROL_IP" =~ ^[0-9]+(\.[0-9]+){3}$ ]]
hostname -I | tr ' ' '\n' | grep -Fxq "$CONTROL_IP"

backend_service='emery-backend.service'
api_service='skryon-api.service'
bot_service='skryon-admin-bot.service'
recovery_service='emery-recovery-agent.service'
live_root='/opt/nt/emery vpn orchestrator'
release_root="${RELEASE_ROOT:-/opt/nt-releases/pr37-20260824-191339/emery vpn orchestrator}"
orchestrator_root='/opt/nt/orchestrator'
runtime_python='/opt/nt/.runtime/modern/bin/python'
orchestrator_python='/opt/nt/orchestrator/.venv/bin/python'
backend_db="$live_root/data/app.db"
orchestrator_db="$orchestrator_root/skryon.db"
environment_file="$live_root/.env"
db_url='sqlite:////opt/nt/emery vpn orchestrator/data/app.db'
node_id=2
node_ip='82.165.163.77'
gate_host='gate1.skryon.ru'
gate_port=8447
gate_spki='c6845703d4c341b731ec684a41402403921de959e85bbce27771083bd6f498cd'
node_host_key_fingerprint='SHA256:IXZX70O+V+7Ef8J+jaI5+6yhIsWmAMcDloAEhOxnU2k'
known_hosts_file='/etc/emery/recovery_known_hosts'

for required_file in "$runtime_python" "$orchestrator_python" "$backend_db" "$orchestrator_db" "$environment_file"; do
  test -s "$required_file"
done
test -f "$release_root/src/backend/main.py"
test -f "$release_root/src/backend/services/pool_assignment_service.py"
release_repo="$(git -C "$release_root" rev-parse --show-toplevel)"
[[ "$(git -C "$release_repo" rev-parse HEAD)" == '85caa7eb9a47c8c5efd0263c526ce38945dc06b5' ]]
git -C "$release_repo" diff --cached --quiet
unexpected_dirty=()
while IFS= read -r -d '' dirty_path; do
  case "$dirty_path" in
    */__pycache__/*.pyc|*/debug-*.log) ;;
    *) unexpected_dirty+=("$dirty_path") ;;
  esac
done < <(git -C "$release_repo" diff --name-only -z)
if (( ${#unexpected_dirty[@]} > 0 )); then
  printf 'Unexpected tracked release changes:\n' >&2
  printf ' - %s\n' "${unexpected_dirty[@]}" >&2
  exit 1
fi
systemctl is-active --quiet "$backend_service"
systemctl is-active --quiet "$api_service"

host_key_scan="$(mktemp)"
trap 'rm -f "$host_key_scan"' EXIT
chmod 600 "$host_key_scan"
ssh-keyscan -T 10 -t ed25519 "$node_ip" > "$host_key_scan" 2>/dev/null
[[ "$(ssh-keygen -lf "$host_key_scan" | awk 'NR == 1 {print $2}')" == "$node_host_key_fingerprint" ]]
[[ "$(awk 'NF >= 3 && $2 == "ssh-ed25519" {count++} END {print count+0}' "$host_key_scan")" == 1 ]]

getent ahostsv4 "$gate_host" | awk '{print $1}' | grep -Fxq "$node_ip"
actual_spki="$({ timeout 12 openssl s_client -connect "$gate_host:$gate_port" -servername "$gate_host" -showcerts </dev/null 2>/dev/null || true; } \
  | openssl x509 -pubkey -noout 2>/dev/null \
  | openssl pkey -pubin -outform DER 2>/dev/null \
  | openssl dgst -sha256 | awk '{print $NF}')"
[[ "$actual_spki" == "$gate_spki" ]]

"$orchestrator_python" - "$environment_file" <<'PY'
import sys
from dotenv import dotenv_values

values = dotenv_values(sys.argv[1])
required = ('POOL_BRIDGE_API_KEY', 'DEVICE_GATE_API_KEY', 'ADMIN_API_KEY')
for name in required:
    value = str(values.get(name) or '').strip()
    if len(value) < 32:
        raise SystemExit(f'{name} is missing or too short')
if str(values.get('POOL_BRIDGE_API_KEY') or '').strip() == str(values.get('DEVICE_GATE_API_KEY') or '').strip():
    raise SystemExit('POOL_BRIDGE_API_KEY and DEVICE_GATE_API_KEY must be distinct')
print('CONTROL_KEYS_OK')
PY

(
  cd "$release_root"
  DB_URL="$db_url" RECOVERY_SSH_KNOWN_HOSTS_PATH="$host_key_scan" \
    EXPECTED_NODE_ID="$node_id" EXPECTED_NODE_IP="$node_ip" \
    EXPECTED_GATE_HOST="$gate_host" EXPECTED_GATE_PORT="$gate_port" \
    EXPECTED_GATE_SPKI="$gate_spki" EXPECTED_HOST_KEY_FINGERPRINT="$node_host_key_fingerprint" \
    "$runtime_python" - <<'PY'
import base64
import hashlib
import os
import socket

from src.backend.services.node_recovery_service import SshAndProviderRecoveryTransport, VlessTcpProbe
from src.common.db import SessionLocal
from src.common.models import VpnNode

node_id = int(os.environ['EXPECTED_NODE_ID'])
node_ip = os.environ['EXPECTED_NODE_IP']
gate_host = os.environ['EXPECTED_GATE_HOST']
gate_port = int(os.environ['EXPECTED_GATE_PORT'])
gate_spki = os.environ['EXPECTED_GATE_SPKI']

db = SessionLocal()
client = None
try:
    node = db.get(VpnNode, node_id)
    if node is None:
        raise RuntimeError('node_missing')
    if node.status != 'active' or node.health_status not in {'healthy', 'degraded'}:
        raise RuntimeError('node_not_eligible')
    if int(node.capacity_clients or 0) < 1:
        raise RuntimeError('invalid_node_capacity')
    if not (1 <= int(node.per_device_speed_limit_mbps or 0) <= 30):
        raise RuntimeError('unsafe_device_speed_limit')
    if (
        node.device_gate_host != gate_host
        or int(node.device_gate_port or 0) != gate_port
        or node.device_gate_server_name != gate_host
        or node.device_gate_spki_sha256 != gate_spki
    ):
        raise RuntimeError('node_gate_metadata_mismatch')
    if not (node.ssh_private_key or '').strip():
        raise RuntimeError('node_ssh_private_key_missing')
    pinned = (node.ssh_host_key or '').strip()
    if pinned:
        try:
            encoded = pinned.split()[1]
            fingerprint = 'SHA256:' + base64.b64encode(
                hashlib.sha256(base64.b64decode(encoded, validate=True)).digest()
            ).decode('ascii').rstrip('=')
        except Exception as exc:
            raise RuntimeError('node_ssh_host_key_invalid') from exc
        if fingerprint != os.environ['EXPECTED_HOST_KEY_FINGERPRINT']:
            raise RuntimeError('node_ssh_host_key_fingerprint_mismatch')
    host, _ = VlessTcpProbe.endpoint(node)
    if not host or socket.gethostbyname(host) != node_ip:
        raise RuntimeError('node_endpoint_mismatch')

    client = SshAndProviderRecoveryTransport()._connect(node)
    command = (
        "set -eu; test \"$(id -u)\" = 0; "
        f"hostname -I | tr ' ' '\\n' | grep -Fxq {node_ip}; "
        "systemctl is-active --quiet xray.service; "
        "systemctl is-active --quiet emery-control-tunnel.service; "
        "systemctl is-active --quiet emery-device-gate.service; "
        "systemctl is-active --quiet emery-assignment-firewall.service; "
        "test -s /usr/local/etc/xray/config.json; command -v nft >/dev/null; "
        "iptables -C INPUT -i lo -p tcp --dport 20000:20199 -m comment --comment EMERY_ASSIGNMENT_LOOPBACK -j ACCEPT; "
        "iptables -C INPUT -p tcp --dport 20000:20199 -m comment --comment EMERY_ASSIGNMENT_PRIVATE -j DROP; "
        f"ss -lntH 'sport = :{gate_port}' | grep -q LISTEN; "
        "curl -fsS --max-time 4 http://127.0.0.1:18081/health >/dev/null"
    )
    _, stdout, stderr = client.exec_command(command, timeout=20)
    error = stderr.read().decode(errors='ignore').strip()
    status = stdout.channel.recv_exit_status()
    if status != 0:
        raise RuntimeError(f'node_remote_preflight_failed:{error[:160]}')
finally:
    if client is not None:
        client.close()
    db.close()
print('NODE_CONTROL_PREFLIGHT_OK')
PY
)

stamp="$(date +%Y%m%d-%H%M%S)"
backup="/root/emery-control-before-enforcement-$stamp"
remote_backup="/root/emery-node-before-enforcement-$stamp"
install -d -m 700 "$backup"

"$runtime_python" - "$backend_db" "$backup/app.db" "$orchestrator_db" "$backup/skryon.db" <<'PY'
import sqlite3
import sys

for source_path, target_path in ((sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[4])):
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
PY

backend_dropin_dir='/etc/systemd/system/emery-backend.service.d'
backend_dropin="$backend_dropin_dir/90-device-gate-enforced.conf"
api_dropin_dir='/etc/systemd/system/skryon-api.service.d'
api_dropin="$api_dropin_dir/90-device-gate-enforced.conf"
had_backend_dropin=0
had_api_dropin=0
if [[ -e "$backend_dropin" ]]; then
  cp -a "$backend_dropin" "$backup/backend-enable.previous"
  had_backend_dropin=1
fi
if [[ -e "$api_dropin" ]]; then
  cp -a "$api_dropin" "$backup/api-enable.previous"
  had_api_dropin=1
fi
cp -a /etc/systemd/system/emery-backend.service "$backup/"
cp -a /etc/systemd/system/skryon-api.service "$backup/"
had_known_hosts=0
if [[ -e "$known_hosts_file" ]]; then
  cp -a "$known_hosts_file" "$backup/recovery_known_hosts.previous"
  had_known_hosts=1
fi
install -d -m 700 /etc/emery
install -m 600 -o root -g root "$host_key_scan" "$known_hosts_file"

bot_was_active=0
recovery_was_active=0
systemctl is-active --quiet "$bot_service" && bot_was_active=1 || true
systemctl is-active --quiet "$recovery_service" && recovery_was_active=1 || true

(
  cd "$release_root"
  DB_URL="$db_url" RECOVERY_SSH_KNOWN_HOSTS_PATH="$known_hosts_file" \
    EXPECTED_NODE_ID="$node_id" REMOTE_BACKUP="$remote_backup" \
    "$runtime_python" - <<'PY'
import os
import re
import shlex

from src.backend.services.node_recovery_service import SshAndProviderRecoveryTransport
from src.common.db import SessionLocal
from src.common.models import VpnNode

remote_backup = os.environ['REMOTE_BACKUP']
if not re.fullmatch(r'/root/emery-node-before-enforcement-[0-9]{8}-[0-9]{6}', remote_backup):
    raise RuntimeError('invalid_remote_backup_path')
db = SessionLocal()
client = None
try:
    node = db.get(VpnNode, int(os.environ['EXPECTED_NODE_ID']))
    if node is None:
        raise RuntimeError('node_missing')
    client = SshAndProviderRecoveryTransport()._connect(node)
    path = shlex.quote(remote_backup)
    command = (
        f"set -eu; install -d -m 700 {path}; "
        f"cp -a /usr/local/etc/xray/config.json {path}/xray-config.json; "
        f"iptables-save > {path}/iptables.v4; "
        f"if nft list table inet emery_vpn_rate > {path}/emery_vpn_rate.nft 2>/dev/null; then :; else : > {path}/emery_vpn_rate.absent; fi"
    )
    _, stdout, stderr = client.exec_command(command, timeout=20)
    error = stderr.read().decode(errors='ignore').strip()
    status = stdout.channel.recv_exit_status()
    if status != 0:
        raise RuntimeError(f'remote_backup_failed:{error[:160]}')
finally:
    if client is not None:
        client.close()
    db.close()
print('REMOTE_XRAY_BACKUP_OK')
PY
)

backend_changed=0
api_changed=0
remote_mutation_possible=0

restore_remote() {
  (
    cd "$release_root"
    DB_URL="$db_url" RECOVERY_SSH_KNOWN_HOSTS_PATH="$known_hosts_file" \
      EXPECTED_NODE_ID="$node_id" REMOTE_BACKUP="$remote_backup" \
      "$runtime_python" - <<'PY'
import os
import shlex

from src.backend.services.node_recovery_service import SshAndProviderRecoveryTransport
from src.common.db import SessionLocal
from src.common.models import VpnNode

db = SessionLocal()
client = None
try:
    node = db.get(VpnNode, int(os.environ['EXPECTED_NODE_ID']))
    if node is None:
        raise RuntimeError('node_missing')
    client = SshAndProviderRecoveryTransport()._connect(node)
    path = shlex.quote(os.environ['REMOTE_BACKUP'])
    command = (
        f"set -eu; xray run -test -config {path}/xray-config.json >/dev/null; "
        f"cp -a {path}/xray-config.json /usr/local/etc/xray/config.json; "
        "nft delete table inet emery_vpn_rate 2>/dev/null || true; "
        f"if test -s {path}/emery_vpn_rate.nft; then nft -f {path}/emery_vpn_rate.nft; fi; "
        "systemctl restart xray.service; systemctl is-active --quiet xray.service"
    )
    _, stdout, stderr = client.exec_command(command, timeout=40)
    error = stderr.read().decode(errors='ignore').strip()
    status = stdout.channel.recv_exit_status()
    if status != 0:
        raise RuntimeError(f'remote_restore_failed:{error[:160]}')
finally:
    if client is not None:
        client.close()
    db.close()
print('REMOTE_XRAY_RESTORED')
PY
  )
}

rollback() {
  code=$?
  trap - ERR
  set +e
  echo "ENFORCEMENT_FAILED: rolling back (code=$code)" >&2
  systemctl stop "$bot_service" "$recovery_service" "$api_service" "$backend_service" >/dev/null 2>&1 || true
  cp -a "$backup/app.db" "$backend_db"
  cp -a "$backup/skryon.db" "$orchestrator_db"
  if [[ "$remote_mutation_possible" == 1 ]]; then
    restore_remote || echo 'WARNING: remote Xray restore needs manual check' >&2
  fi
  rm -f "$known_hosts_file"
  [[ "$had_known_hosts" == 1 ]] && cp -a "$backup/recovery_known_hosts.previous" "$known_hosts_file"
  if [[ "$backend_changed" == 1 ]]; then
    rm -f "$backend_dropin"
    [[ "$had_backend_dropin" == 1 ]] && cp -a "$backup/backend-enable.previous" "$backend_dropin"
  fi
  if [[ "$api_changed" == 1 ]]; then
    rm -f "$api_dropin"
    [[ "$had_api_dropin" == 1 ]] && cp -a "$backup/api-enable.previous" "$api_dropin"
  fi
  systemctl daemon-reload
  systemctl start "$backend_service" "$api_service" >/dev/null 2>&1 || true
  [[ "$recovery_was_active" == 1 ]] && systemctl start "$recovery_service" >/dev/null 2>&1 || true
  [[ "$bot_was_active" == 1 ]] && systemctl start "$bot_service" >/dev/null 2>&1 || true
  echo "Rollback backup: $backup; node backup: $remote_backup" >&2
  exit "$code"
}
trap rollback ERR

[[ "$bot_was_active" == 1 ]] && systemctl stop "$bot_service"
[[ "$recovery_was_active" == 1 ]] && systemctl stop "$recovery_service"
systemctl stop "$api_service"

install -d -m 755 "$backend_dropin_dir" "$api_dropin_dir"
cat > "$backend_dropin" <<'UNIT'
[Service]
Environment=POOL_ACCOUNTING_BRIDGE_ENABLED=true
Environment=UNIQUE_DEVICE_CREDENTIALS_ENABLED=true
Environment=PER_DEVICE_RATE_LIMIT_ENFORCED=true
Environment=SMTP_ABUSE_PROTECTION_ENABLED=true
Environment=DEVICE_BOUND_GATE_ENABLED=true
Environment=MIN_SUPPORTED_APP_VERSION_CODE=718
Environment=RECOVERY_SSH_KNOWN_HOSTS_PATH=/etc/emery/recovery_known_hosts
UNIT
backend_changed=1

cat > "$api_dropin" <<'UNIT'
[Service]
Environment=POOL_BRIDGE_ENABLED=true
Environment=MIN_SUPPORTED_APP_VERSION_CODE=718
UNIT
api_changed=1

systemctl daemon-reload
remote_mutation_possible=1
systemctl restart "$backend_service"

backend_ready=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:9330/api/v1/ready >/dev/null; then
    backend_ready=1
    break
  fi
  sleep 1
done
[[ "$backend_ready" == 1 ]]

backend_pid="$(systemctl show "$backend_service" -p MainPID --value)"
for expected in \
  POOL_ACCOUNTING_BRIDGE_ENABLED=true \
  UNIQUE_DEVICE_CREDENTIALS_ENABLED=true \
  PER_DEVICE_RATE_LIMIT_ENFORCED=true \
  SMTP_ABUSE_PROTECTION_ENABLED=true \
  DEVICE_BOUND_GATE_ENABLED=true \
  MIN_SUPPORTED_APP_VERSION_CODE=718 \
  RECOVERY_SSH_KNOWN_HOSTS_PATH=/etc/emery/recovery_known_hosts; do
  tr '\0' '\n' < "/proc/$backend_pid/environ" | grep -Fxq "$expected"
done

"$runtime_python" - "$environment_file" <<'PY'
import json
import sys

import httpx
from dotenv import dotenv_values

key = str(dotenv_values(sys.argv[1]).get('POOL_BRIDGE_API_KEY') or '').strip()
headers = {'X-Pool-Bridge-Key': key}
url = 'http://127.0.0.1:9330/api/v1/internal/pool/assignments/maintenance'
results = []
for run in (1, 2):
    response = httpx.post(url, headers=headers, timeout=300)
    if response.status_code != 200:
        raise RuntimeError(f'maintenance_{run}_http_{response.status_code}:{response.text[:200]}')
    data = response.json()
    if int(data.get('failed', -1)) != 0:
        raise RuntimeError(f'maintenance_{run}_failed:{data}')
    if run == 2 and int(data.get('migrated', -1)) != 0:
        raise RuntimeError(f'maintenance_not_idempotent:{data}')
    results.append(data)
print('MAINTENANCE_OK', json.dumps(results, ensure_ascii=False, separators=(',', ':')))
PY

systemctl start "$api_service"
api_ready=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:8080/health >/dev/null; then
    api_ready=1
    break
  fi
  sleep 1
done
[[ "$api_ready" == 1 ]]

api_pid="$(systemctl show "$api_service" -p MainPID --value)"
tr '\0' '\n' < "/proc/$api_pid/environ" | grep -Fxq 'POOL_BRIDGE_ENABLED=true'
tr '\0' '\n' < "/proc/$api_pid/environ" | grep -Fxq 'MIN_SUPPORTED_APP_VERSION_CODE=718'

(
  cd "$orchestrator_root"
  POOL_BRIDGE_ENABLED=true MIN_SUPPORTED_APP_VERSION_CODE=718 "$orchestrator_python" - <<'PY'
import httpx
import pool_reservation_bridge

if not pool_reservation_bridge.is_enabled():
    raise RuntimeError('pool_bridge_not_enabled')

old = httpx.post(
    'http://127.0.0.1:8080/api/activate',
    json={'code': 'X', 'deviceId': 'test', 'appVersionCode': 717},
    timeout=10,
)
if old.status_code != 200 or old.json().get('reason') != 'upgrade_required':
    raise RuntimeError('old_app_not_blocked')

unauthorized = httpx.post(
    'http://127.0.0.1:8080/internal/device-gate/authorize',
    json={
        'assignment_id': 1,
        'node_id': 2,
        'gate_server_name': 'gate1.skryon.ru',
        'gate_spki_sha256': 'c6845703d4c341b731ec684a41402403921de959e85bbce27771083bd6f498cd',
        'device_id': 'test',
        'server_issued_at': '0',
        'timestamp': '0',
        'server_nonce': '0000000000000000',
        'client_nonce': '1111111111111111',
        'signature': '0000000000000000',
        'signature_algorithm': 'SHA256withECDSA',
    },
    timeout=10,
)
if unauthorized.status_code != 403 or unauthorized.json().get('reason') != 'device_gate_forbidden':
    raise RuntimeError('unauthorized_gate_request_not_blocked')
print('SECURITY_NEGATIVE_TESTS_OK')
PY
)

[[ "$recovery_was_active" == 1 ]] && systemctl start "$recovery_service"
[[ "$bot_was_active" == 1 ]] && systemctl start "$bot_service"
systemctl is-active --quiet "$backend_service"
systemctl is-active --quiet "$api_service"
[[ "$bot_was_active" == 0 ]] || systemctl is-active --quiet "$bot_service"
[[ "$recovery_was_active" == 0 ]] || systemctl is-active --quiet "$recovery_service"

trap - ERR
echo "CONTROL_PROTECTION_ENABLED: version 718 required; pool bridge and device gate ON; backup=$backup; node_backup=$remote_backup"
