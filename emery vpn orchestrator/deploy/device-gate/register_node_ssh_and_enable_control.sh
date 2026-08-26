#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

: "${CONTROL_IP:?CONTROL_IP is required}"
[[ "$CONTROL_IP" == '31.70.76.155' ]]
hostname -I | tr ' ' '\n' | grep -Fxq "$CONTROL_IP"

node_id=2
node_ip='82.165.163.77'
key_path='/etc/emery/recovery-keys/node-2'
public_key_path="${key_path}.pub"
expected_key_fingerprint='SHA256:PVSBlAO8RvTytYljf1sugboFl62sknODEc3SQ5G1RxE'
expected_host_fingerprint='SHA256:IXZX70O+V+7Ef8J+jaI5+6yhIsWmAMcDloAEhOxnU2k'
database='/opt/nt/emery vpn orchestrator/data/app.db'
enable_commit='ee9c2180121b937e1f770524e5d3b8038b64e029'
enable_sha256='c24dca5c9d2f27539061c9db295c07ac83a599dbd9aaefece871fd59e079529e'
enable_url="https://raw.githubusercontent.com/mafimusic5-beep/nt/${enable_commit}/emery%20vpn%20orchestrator/deploy/device-gate/enable_device_bound_gate_control.sh"

test -s "$key_path"
test -s "$public_key_path"
test -s "$database"
[[ "$(stat -c '%U:%G:%a' "$key_path")" == 'root:root:600' ]]
[[ "$(ssh-keygen -lf "$public_key_path" | awk 'NR == 1 {print $2}')" == "$expected_key_fingerprint" ]]

derived_public_blob="$(ssh-keygen -y -f "$key_path" | awk 'NR == 1 {print $2}')"
stored_public_blob="$(awk 'NR == 1 {print $2}' "$public_key_path")"
[[ -n "$derived_public_blob" ]]
[[ "$derived_public_blob" == "$stored_public_blob" ]]
[[ "$(wc -l < "$public_key_path")" == 1 ]]

host_keys="$(mktemp)"
enable_script="$(mktemp)"
trap 'rm -f "$host_keys" "$enable_script"' EXIT
chmod 600 "$host_keys" "$enable_script"
ssh-keyscan -T 10 -t ed25519 "$node_ip" > "$host_keys" 2>/dev/null
[[ "$(awk 'NF >= 3 && $2 == "ssh-ed25519" {count++} END {print count+0}' "$host_keys")" == 1 ]]
[[ "$(ssh-keygen -lf "$host_keys" | awk 'NR == 1 {print $2}')" == "$expected_host_fingerprint" ]]

ssh \
  -i "$key_path" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$host_keys" \
  -o ConnectTimeout=10 \
  "root@$node_ip" \
  "test \"\$(id -u)\" = 0 && systemctl is-active --quiet xray.service && systemctl is-active --quiet emery-device-gate.service && echo NODE_RECOVERY_SSH_OK"

stamp="$(date +%Y%m%d-%H%M%S)"
database_backup="/root/app.db.before-node-2-recovery-key-${stamp}"

DB_PATH="$database" DB_BACKUP="$database_backup" KEY_PATH="$key_path" \
PUBLIC_KEY_PATH="$public_key_path" HOST_KEYS_PATH="$host_keys" \
EXPECTED_KEY_FINGERPRINT="$expected_key_fingerprint" NODE_ID="$node_id" \
python3 - <<'PY'
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

db_path = os.environ['DB_PATH']
backup_path = os.environ['DB_BACKUP']
node_id = int(os.environ['NODE_ID'])
private_key = Path(os.environ['KEY_PATH']).read_text(encoding='utf-8')
public_key = Path(os.environ['PUBLIC_KEY_PATH']).read_text(encoding='utf-8').strip()
host_key_parts = Path(os.environ['HOST_KEYS_PATH']).read_text(encoding='utf-8').strip().split()

if not private_key.startswith('-----BEGIN OPENSSH PRIVATE KEY-----'):
    raise RuntimeError('unexpected_private_key_format')
if len(public_key.splitlines()) != 1 or not public_key.startswith('ssh-ed25519 '):
    raise RuntimeError('unexpected_public_key_format')
if len(host_key_parts) != 3 or host_key_parts[1] != 'ssh-ed25519':
    raise RuntimeError('unexpected_host_key_format')
host_key = f'{host_key_parts[1]} {host_key_parts[2]}'

source = sqlite3.connect(db_path, timeout=30)
backup = sqlite3.connect(backup_path)
try:
    source.backup(backup)
finally:
    backup.close()

try:
    columns = {row[1] for row in source.execute('pragma table_info(vpn_nodes)')}
    required = {
        'id', 'ssh_private_key', 'ssh_public_key', 'ssh_key_fingerprint',
        'ssh_key_status', 'ssh_host_key',
    }
    if not required.issubset(columns):
        raise RuntimeError(f'missing_node_ssh_columns:{sorted(required - columns)}')
    row = source.execute(
        'select ssh_private_key, ssh_public_key from vpn_nodes where id=?',
        (node_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError('node_missing')
    existing_private = str(row[0] or '').strip()
    existing_public = str(row[1] or '').strip()
    if existing_private and existing_private != private_key.strip():
        raise RuntimeError('different_private_key_already_registered')
    if existing_public and existing_public != public_key:
        raise RuntimeError('different_public_key_already_registered')

    assignments = [
        'ssh_private_key=?', 'ssh_public_key=?', 'ssh_key_fingerprint=?',
        'ssh_key_status=?', 'ssh_host_key=?',
    ]
    values = [
        private_key.strip() + '\n', public_key,
        os.environ['EXPECTED_KEY_FINGERPRINT'], 'installed', host_key,
    ]
    if 'updated_at' in columns:
        assignments.append('updated_at=?')
        values.append(datetime.now(timezone.utc).isoformat())
    values.append(node_id)
    source.execute('begin immediate')
    result = source.execute(
        f"update vpn_nodes set {', '.join(assignments)} where id=?",
        values,
    )
    if result.rowcount != 1:
        raise RuntimeError('node_update_failed')
    source.commit()
    check = source.execute(
        'select ssh_key_status, ssh_key_fingerprint, ssh_host_key, length(ssh_private_key) '
        'from vpn_nodes where id=?',
        (node_id,),
    ).fetchone()
    if (
        check is None
        or check[0] != 'installed'
        or check[1] != os.environ['EXPECTED_KEY_FINGERPRINT']
        or check[2] != host_key
        or int(check[3] or 0) < 100
    ):
        raise RuntimeError('node_key_verification_failed')
except Exception:
    source.rollback()
    raise
finally:
    source.close()

print(f'NODE_RECOVERY_DB_READY backup={backup_path}')
PY

chmod 600 "$database_backup"
curl -fsSL --retry 3 "$enable_url" -o "$enable_script"
echo "$enable_sha256  $enable_script" | sha256sum -c -
CONTROL_IP="$CONTROL_IP" bash "$enable_script"
