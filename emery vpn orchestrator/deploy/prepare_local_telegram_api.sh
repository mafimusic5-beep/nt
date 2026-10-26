#!/usr/bin/env bash
# Stage (do not switch the Skryon bot) a loopback-only Telegram Bot API Server.
# Run on the ACTUAL bot host 31.70.76.155 as root.
set -Eeuo pipefail
umask 077
if [[ "$(id -u)" -ne 0 ]]; then echo "ROOT_REQUIRED"; exit 10; fi
bin="$(command -v telegram-bot-api || true)"
[[ -n "$bin" && -x "$bin" ]] || { echo "OFFICIAL_TELEGRAM_BOT_API_BINARY_NOT_INSTALLED"; exit 11; }
env_file=/etc/skryon/telegram-bot-api.env
[[ -f "$env_file" && "$(stat -c %a "$env_file")" == 600 ]] || {
  echo "MISSING_PRIVATE_TELEGRAM_API_ID_AND_HASH_AT_$env_file"
  exit 12
}
# Parse credential file using shlex for VALIDATION ONLY; never print secrets.
python3 - "$env_file" <<'PY'
import pathlib, re, shlex, sys
env = pathlib.Path(sys.argv[1]).read_text()
values = {}
for row in env.splitlines():
    row = row.strip()
    if not row or row.startswith('#'): continue
    k, sep, value = row.partition('=')
    if not sep or k not in ('TELEGRAM_API_ID','TELEGRAM_API_HASH'):
        raise SystemExit('INVALID_LOCAL_API_ENV_FORMAT')
    values[k] = value.strip().strip('"').strip("'")
if not (values.get('TELEGRAM_API_ID','').isdigit() and
        int(values['TELEGRAM_API_ID']) > 0 and
        re.fullmatch(r'[0-9a-fA-F]{32}', values.get('TELEGRAM_API_HASH',''))):
    raise SystemExit('LOCAL_API_ID_HASH_MISSING_OR_MALFORMED')
print('LOCAL_API_CREDENTIAL_SHAPE_OK')
PY
systemctl is-active --quiet skryon-bot.service || {
  echo "EXISTING_BOT_NOT_RUNNING; ABORT"
  exit 13
}
if systemctl list-unit-files telegram-bot-api.service --no-legend 2>/dev/null | grep -q '^telegram-bot-api.service'; then
  echo "LOCAL_API_UNIT_ALREADY_EXISTS_REVIEW_BEFORE_REPLACING"; exit 14
fi
if ss -H -ltn '( sport = :8081 )' | grep -q .; then
  echo "LOCAL_API_PORT_8081_OCCUPIED"; exit 15
fi
id -u skryon-tg-api >/dev/null 2>&1 || useradd --system --home-dir /var/lib/skryon-telegram-api --shell /usr/sbin/nologin skryon-tg-api
install -d -o skryon-tg-api -g skryon-tg-api -m 0700 /var/lib/skryon-telegram-api
install -d -o root -g root -m 0700 /etc/skryon
cat > /etc/systemd/system/telegram-bot-api.service <<UNIT
[Unit]
Description=Skryon Local Telegram Bot API (127.0.0.1 only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=skryon-tg-api
Group=skryon-tg-api
WorkingDirectory=/var/lib/skryon-telegram-api
EnvironmentFile=$env_file
ExecStart=$bin --local --http-ip-address=127.0.0.1 --http-port=8081 --dir=/var/lib/skryon-telegram-api
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/skryon-telegram-api
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
UNIT
# The bot service must be able to read local getFile() paths. Currently it
# runs as root; otherwise a shared group and data-dir ACL are required.
systemctl daemon-reload
systemd-analyze verify telegram-bot-api.service
systemctl start telegram-bot-api.service
sleep 2
if ! systemctl is-active --quiet telegram-bot-api.service; then
  echo "LOCAL_API_SERVICE_FAILED; bot not switched"
  exit 16
fi
if ! ss -H -ltn '( sport = :8081 )' | grep -q '127.0.0.1:8081'; then
  echo "NOT_BOUND_TO_LOOPBACK; STOPPING"
  systemctl stop telegram-bot-api.service
  exit 17
fi
echo "LOCAL_TELEGRAM_API_READY_ON_LOOPBACK"
echo "EXISTING_SKRYON_BOT_NOT_SWITCHED"
echo "NEXT=Review switchover script; cloud logOut has a ten-minute return restriction"
