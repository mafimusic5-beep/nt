#!/usr/bin/env bash
# Change ONLY the Windows Telegram upload handlers, never the VPN API.
set -Eeuo pipefail
umask 077
src="${1:?Provide the checked-out repo root}"
modern="/opt/nt/emery vpn orchestrator"
python="/opt/nt/.runtime/modern/bin/python"
service="skryon-bot.service"
dir="$src/emery vpn orchestrator/src/bot"
items=(
 "handlers/windows_releases.py"
 "utils/windows_release_store.py"
 "utils/windows_upload_chunks.py"
)
[[ -x "$python" && -d "$modern/src/bot" ]] || { echo 'NOT_BOT_RUNTIME'; exit 81; }
systemctl is-active --quiet "$service" || { echo 'BOT_NOT_ACTIVE'; exit 82; }
systemctl cat "$service" --no-pager | grep -q 'src.bot.main' || {
 echo 'WRONG_BOT_SERVICE'; exit 83;
}
for f in "${items[@]}"; do [[ -f "$dir/$f" ]] || {
  echo "MISSING_STAGE=$f"; exit 84;
}; done
"$python" -m compileall -q "$dir/handlers/windows_releases.py" "$dir/utils/windows_release_store.py" "$dir/utils/windows_upload_chunks.py"
# Save exact live code and handle partial deploys with a rollback.
backup="/opt/skryon-bot-admin-backups/telegram-chunks-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
for f in "${items[@]}"; do
  mkdir -p "$backup/$(dirname "$f")"
  if [[ -e "$modern/src/bot/$f" ]]; then cp -p "$modern/src/bot/$f" "$backup/$f"; fi
done
changed=0
rollback() {
  code=$?
  trap - ERR
  if [[ "$changed" == 1 ]]; then
    for f in "${items[@]}"; do
      if [[ -f "$backup/$f" ]]; then cp -p "$backup/$f" "$modern/src/bot/$f"; else rm -f "$modern/src/bot/$f"; fi
    done
    systemctl restart "$service" >/dev/null 2>&1 || true
    echo 'TELEGRAM_CHUNKS_ROLLED_BACK'
  fi
  exit "$code"
}
trap rollback ERR
changed=1
for f in "${items[@]}"; do
 mkdir -p "$modern/src/bot/$(dirname "$f")"
 install -m 0644 "$dir/$f" "$modern/src/bot/$f"
done
cd "$modern"
"$python" -m py_compile src/bot/handlers/windows_releases.py src/bot/utils/windows_release_store.py src/bot/utils/windows_upload_chunks.py
"$python" -c 'from src.bot.handlers.windows_releases import router; from src.bot.utils.windows_upload_chunks import parse; assert parse("SkryonSetup-1.0.0.exe."+"a"*64+".part001of006.skpart").total == 6; assert router.name == "windows_releases"'
systemctl restart "$service"
sleep 3
systemctl is-active --quiet "$service"
pid="$(systemctl show "$service" -p MainPID --value)"
[[ "$pid" =~ ^[1-9][0-9]*$ ]]
changed=0
echo "SKRYON_TELEGRAM_CHUNKS_ACTIVE PID=$pid"
echo "BACKUP=$backup"
