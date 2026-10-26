#!/usr/bin/env bash
# Deploy ONLY the owner Telegram bot admin UI. Does NOT change the VPN API,
# the database, site, DNS, update minimum, gateway, or existing subscriptions.
set -Eeuo pipefail
umask 077

source_tree="${1:?Pass the checked-out repository root}"
live=/opt/nt
service=skryon-bot.service
modern="$live/emery vpn orchestrator"
runtime="$live/.runtime/modern/bin/python"
backup_root=/opt/skryon-bot-admin-backups
backup="$backup_root/$(date -u +%Y%m%dT%H%M%SZ)"
items=(
  "src/bot/main.py"
  "src/bot/ui/keyboards.py"
  "src/bot/handlers/windows_releases.py"
  "src/bot/handlers/admin_guided.py"
  "src/bot/utils/windows_release_store.py"
)
[[ -x "$runtime" && -d "$modern/src/bot" ]] || { echo 'WRONG_HOST_OR_RUNTIME'; exit 81; }
systemctl is-active --quiet "$service" || { echo 'LIVE_BOT_NOT_ACTIVE'; exit 82; }
# Never accidentally install a second poller or update a legacy-only service.
systemctl cat "$service" --no-pager | grep -q 'src.bot.main' || {
  echo 'SERVICE_DOES_NOT_RUN_MODERN_BOT'; exit 83;
}
source_modern="$source_tree/emery vpn orchestrator"
for item in "${items[@]}"; do
  [[ -f "$source_modern/$item" ]] || { echo "MISSING_STAGE_FILE: $item"; exit 84; }
done
# Refuse overwriting production's uncommitted modifications to critical files.
for item in "${items[@]}"; do
  if [[ -e "$modern/$item" ]] && ! git -C "$live" diff --quiet -- "emery vpn orchestrator/$item"; then
    echo "LIVE_MODIFIED_FILE_REQUIRES_REVIEW: $item"
    exit 85
  fi
done
"$runtime" -m compileall -q "$source_modern/src/bot"
mkdir -p "$backup"
for item in "${items[@]}"; do
  mkdir -p "$backup/$(dirname "$item")"
  [[ ! -f "$modern/$item" ]] || cp -p "$modern/$item" "$backup/$item"
done
printf '%s\n' "$backup" > "$backup_root/last-backup-path"
changed=0
rollback() {
  status=$?
  trap - ERR
  if [[ "$changed" -eq 1 ]]; then
    for item in "${items[@]}"; do
      if [[ -f "$backup/$item" ]]; then
        cp -p "$backup/$item" "$modern/$item"
      else
        rm -f "$modern/$item"
      fi
    done
    systemctl restart "$service" >/dev/null 2>&1 || true
    echo 'BOT_CODE_ROLLED_BACK'
  fi
  exit "$status"
}
trap rollback ERR
# Compatible PE parsing is only used when uploading a release file.
"$runtime" -m pip install -q 'pefile==2024.8.26'
changed=1
for item in "${items[@]}"; do
  mkdir -p "$modern/$(dirname "$item")"
  install -m 0644 "$source_modern/$item" "$modern/$item"
done
cd "$modern"
"$runtime" -m compileall -q src/bot
"$runtime" -c 'from src.bot.handlers.windows_releases import router; from src.bot.handlers.admin_guided import router as a; from src.bot.ui.keyboards import admin_menu_keyboard; from src.bot.main import main; assert any(b.callback_data == "admin_releases" for row in admin_menu_keyboard().inline_keyboard for b in row)'
systemctl restart "$service"
sleep 3
systemctl is-active --quiet "$service"
pid="$(systemctl show "$service" --property=MainPID --value)"
[[ "$pid" =~ ^[1-9][0-9]*$ ]]
changed=0
echo "SKRYON_BOT_ADMIN_ACTIVE"
echo "ROLLBACK_SOURCE=$backup"
echo "BOT_MAINPID=$pid"
