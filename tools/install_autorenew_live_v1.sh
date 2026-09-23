#!/usr/bin/env bash
set -Eeuo pipefail

LIVE=/opt/nt/orchestrator
VENV="$LIVE/.venv/bin/python"
BASE=https://raw.githubusercontent.com/mafimusic5-beep/nt/main
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="/root/skryon-autorenew-v1-$STAMP"
STAGE=$(mktemp -d /tmp/skryon-autorenew-install.XXXXXX)
APPLIED=0

cleanup() {
  rm -rf "$STAGE"
}
rollback() {
  rc=$?
  trap - ERR
  if [[ "$APPLIED" == 1 ]]; then
    echo "ROLLBACK: restoring $BACKUP" >&2
    cp -a "$BACKUP/payment_routes.py" "$LIVE/payment_routes.py"
    cp -a "$BACKUP/yookassa_checkout.py" "$LIVE/yookassa_checkout.py"
    [[ -f "$BACKUP/autorenew.py" ]] && cp -a "$BACKUP/autorenew.py" "$LIVE/autorenew.py" || rm -f "$LIVE/autorenew.py"
    [[ -f "$BACKUP/autorenew_worker.py" ]] && cp -a "$BACKUP/autorenew_worker.py" "$LIVE/autorenew_worker.py" || rm -f "$LIVE/autorenew_worker.py"
    [[ -f "$BACKUP/skryon.db" ]] && cp -a "$BACKUP/skryon.db" "$LIVE/skryon.db" || true
    [[ -f "$BACKUP/skryon-autorenew.service" ]] && cp -a "$BACKUP/skryon-autorenew.service" /etc/systemd/system/skryon-autorenew.service || rm -f /etc/systemd/system/skryon-autorenew.service
    [[ -f "$BACKUP/skryon-autorenew.timer" ]] && cp -a "$BACKUP/skryon-autorenew.timer" /etc/systemd/system/skryon-autorenew.timer || rm -f /etc/systemd/system/skryon-autorenew.timer
    systemctl daemon-reload || true
    systemctl disable --now skryon-autorenew.timer 2>/dev/null || true
    systemctl restart skryon-api.service || true
  fi
  cleanup
  exit "$rc"
}
trap rollback ERR
trap cleanup EXIT

[[ -f "$LIVE/payment_routes.py" ]]
[[ -f "$LIVE/yookassa_checkout.py" ]]
[[ -x "$VENV" ]]
mkdir -p "$BACKUP"

curl -fsSL "$BASE/orchestrator/autorenew.py" -o "$STAGE/autorenew.py"
curl -fsSL "$BASE/orchestrator/autorenew_worker.py" -o "$STAGE/autorenew_worker.py"
curl -fsSL "$BASE/tools/apply_autorenew_v1.py" -o "$STAGE/apply_autorenew_v1.py"
cp -a "$LIVE/payment_routes.py" "$STAGE/payment_routes.py"
cp -a "$LIVE/yookassa_checkout.py" "$STAGE/yookassa_checkout.py"

BEFORE_METHODS=$(grep -c 'paymentMethod' "$LIVE/payment_routes.py" || true)
python3 "$STAGE/apply_autorenew_v1.py" "$STAGE"
python3 -m py_compile "$STAGE/payment_routes.py" "$STAGE/yookassa_checkout.py" "$STAGE/autorenew.py" "$STAGE/autorenew_worker.py"
AFTER_METHODS=$(grep -c 'paymentMethod' "$STAGE/payment_routes.py" || true)
[[ "$BEFORE_METHODS" == "$AFTER_METHODS" ]]

cp -a "$LIVE/payment_routes.py" "$BACKUP/payment_routes.py"
cp -a "$LIVE/yookassa_checkout.py" "$BACKUP/yookassa_checkout.py"
[[ -f "$LIVE/autorenew.py" ]] && cp -a "$LIVE/autorenew.py" "$BACKUP/autorenew.py" || true
[[ -f "$LIVE/autorenew_worker.py" ]] && cp -a "$LIVE/autorenew_worker.py" "$BACKUP/autorenew_worker.py" || true
[[ -f /etc/systemd/system/skryon-autorenew.service ]] && cp -a /etc/systemd/system/skryon-autorenew.service "$BACKUP/skryon-autorenew.service" || true
[[ -f /etc/systemd/system/skryon-autorenew.timer ]] && cp -a /etc/systemd/system/skryon-autorenew.timer "$BACKUP/skryon-autorenew.timer" || true
if [[ -f "$LIVE/skryon.db" ]]; then
  "$VENV" - "$LIVE/skryon.db" "$BACKUP/skryon.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
print('DB_BACKUP_OK')
PY
fi

cp "$STAGE/payment_routes.py" "$LIVE/payment_routes.py"
cp "$STAGE/yookassa_checkout.py" "$LIVE/yookassa_checkout.py"
cp "$STAGE/autorenew.py" "$LIVE/autorenew.py"
cp "$STAGE/autorenew_worker.py" "$LIVE/autorenew_worker.py"
chmod 0644 "$LIVE/autorenew.py" "$LIVE/autorenew_worker.py"
APPLIED=1

cat >/etc/systemd/system/skryon-autorenew.service <<'UNIT'
[Unit]
Description=Skryon YooKassa autorenew worker
After=network-online.target skryon-api.service
Wants=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/nt/orchestrator
ExecStart=/opt/nt/orchestrator/.venv/bin/python /opt/nt/orchestrator/autorenew_worker.py
Nice=10
UNIT

cat >/etc/systemd/system/skryon-autorenew.timer <<'UNIT'
[Unit]
Description=Run Skryon autorenew worker hourly

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
RandomizedDelaySec=5min
Persistent=true
Unit=skryon-autorenew.service

[Install]
WantedBy=timers.target
UNIT

cd "$LIVE"
"$VENV" -m py_compile payment_routes.py yookassa_checkout.py autorenew.py autorenew_worker.py
"$VENV" -c "from autorenew import ensure_autorenew_storage; ensure_autorenew_storage(); print('AUTORENEW_SCHEMA_OK')"
systemctl daemon-reload
systemctl enable --now skryon-autorenew.timer
systemctl restart skryon-api.service
systemctl is-active --quiet skryon-api.service
systemctl is-active --quiet skryon-autorenew.timer

READY=0
for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8080/api/checkout/payment-ready >/tmp/skryon-payment-ready-autorenew.json 2>/dev/null; then
    READY=1
    break
  fi
  sleep 1
done
[[ "$READY" == 1 ]]
cat /tmp/skryon-payment-ready-autorenew.json
grep -q '"autorenew"' /tmp/skryon-payment-ready-autorenew.json

DISABLE=$(curl -fsS -X POST http://127.0.0.1:8080/api/checkout/autorenew/disable \
  -H 'Content-Type: application/json' \
  --data '{"token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}')
echo "$DISABLE"
[[ "$DISABLE" == *'"ok":true'* ]]

"$VENV" autorenew_worker.py
grep -q 'SKRYON_AUTORENEW_ROUTES_V1' payment_routes.py
grep -q 'SKRYON_AUTORENEW_YOOKASSA_V1' yookassa_checkout.py
grep -q 'save_payment_method' yookassa_checkout.py

echo "BACKEND_AUTORENEW_INSTALLED backup=$BACKUP"
echo "NOTE: feature stays hidden until YOOKASSA_AUTORENEW_ENABLED=1 is configured."
APPLIED=0
