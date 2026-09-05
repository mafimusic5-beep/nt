#!/usr/bin/env bash
set -Eeuo pipefail

: "${CONTROL_IP:?CONTROL_IP is required}"
[[ "$CONTROL_IP" =~ ^[0-9.]+$ ]]
hostname -I | grep -qw "$CONTROL_IP"

service_name="emery-backend.service"
live_root="/opt/nt/emery vpn orchestrator"
release_root="${RELEASE_ROOT:-$live_root}"
runtime_python="/opt/nt/.runtime/modern/bin/python"
runtime_uvicorn="/opt/nt/.runtime/modern/bin/uvicorn"
database="$live_root/data/app.db"
environment_file="$live_root/.env"
dropin_dir="/etc/systemd/system/emery-backend.service.d"
dropin_file="$dropin_dir/50-pr37-device-gate.conf"

test -x "$runtime_python"
test -x "$runtime_uvicorn"
test -s "$database"
test -s "$environment_file"
test -f "$release_root/alembic.ini"
test -f "$release_root/src/backend/api/routes.py"
test -f "$release_root/alembic/versions/0010_device_bound_vless_gate.py"
grep -Fq '"/admin/nodes/{node_id}/device-gate"' "$release_root/src/backend/api/routes.py"
systemctl is-active --quiet "$service_name"
"$runtime_python" -m alembic --version >/dev/null

backup="/root/emery-backend-before-device-gate-$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$backup"
"$runtime_python" - "$database" "$backup/app.db" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
cp -a /etc/systemd/system/emery-backend.service "$backup/"
if [[ -e "$dropin_file" ]]; then
  cp -a "$dropin_file" "$backup/50-pr37-device-gate.conf.previous"
  had_previous_dropin=1
else
  had_previous_dropin=0
fi

database_changed=0
dropin_changed=0
rollback() {
  code=$?
  trap - ERR
  set +e
  echo "DEPLOY_FAILED: rolling back (code=$code)" >&2
  systemctl stop "$service_name" >/dev/null 2>&1 || true
  if [[ "$database_changed" == 1 ]]; then
    cp -a "$backup/app.db" "$database"
  fi
  if [[ "$dropin_changed" == 1 ]]; then
    rm -f "$dropin_file"
    if [[ "$had_previous_dropin" == 1 ]]; then
      cp -a "$backup/50-pr37-device-gate.conf.previous" "$dropin_file"
    fi
  fi
  systemctl daemon-reload
  systemctl start "$service_name" >/dev/null 2>&1 || true
  echo "Rollback backup: $backup" >&2
  exit "$code"
}
trap rollback ERR

db_url="sqlite:////opt/nt/emery vpn orchestrator/data/app.db"
(
  cd "$release_root"
  DB_URL="$db_url" DEVICE_BOUND_GATE_ENABLED=false UNIQUE_DEVICE_CREDENTIALS_ENABLED=false \
    MIN_SUPPORTED_APP_VERSION_CODE=0 "$runtime_python" -c 'import src.backend.main'
)

systemctl stop "$service_name"
database_changed=1
(
  cd "$release_root"
  DB_URL="$db_url" DEVICE_BOUND_GATE_ENABLED=false UNIQUE_DEVICE_CREDENTIALS_ENABLED=false \
    MIN_SUPPORTED_APP_VERSION_CODE=0 "$runtime_python" -m alembic -c alembic.ini upgrade head
)

install -d -m 755 "$dropin_dir"
cat > "$dropin_file" <<EOF
[Service]
Environment=DEVICE_BOUND_GATE_ENABLED=false
Environment=UNIQUE_DEVICE_CREDENTIALS_ENABLED=false
Environment=MIN_SUPPORTED_APP_VERSION_CODE=0
ExecStart=
ExecStart=$runtime_python $runtime_uvicorn src.backend.main:app --app-dir "$release_root" --host 127.0.0.1 --port 9330
EOF
dropin_changed=1
systemctl daemon-reload
systemctl start "$service_name"

ready=0
for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 http://127.0.0.1:9330/api/v1/ready >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
[[ "$ready" == 1 ]]

"$runtime_python" - "$environment_file" <<'PY'
import json
import sys

import httpx
from dotenv import dotenv_values

key = (dotenv_values(sys.argv[1]).get("ADMIN_API_KEY") or "").strip()
if not key:
    raise SystemExit("ADMIN_API_KEY is missing")
payload = {
    "device_gate_host": "gate1.skryon.ru",
    "device_gate_port": 8447,
    "device_gate_server_name": "gate1.skryon.ru",
    "device_gate_spki_sha256": "c6845703d4c341b731ec684a41402403921de959e85bbce27771083bd6f498cd",
}
response = httpx.put(
    "http://127.0.0.1:9330/api/v1/admin/nodes/2/device-gate",
    headers={"X-Admin-Api-Key": key},
    json=payload,
    timeout=15,
)
if response.status_code != 200:
    raise SystemExit(f"device gate registration failed: HTTP {response.status_code} {response.text}")
data = response.json()
for field, expected in payload.items():
    if data.get(field) != expected:
        raise SystemExit(f"unexpected {field}: {data.get(field)!r}")
print(json.dumps({field: data.get(field) for field in payload}, ensure_ascii=False))
PY

systemctl is-active --quiet "$service_name"
trap - ERR
echo "BACKEND_READY: node 2 gate registered; protection remains OFF; backup=$backup"
