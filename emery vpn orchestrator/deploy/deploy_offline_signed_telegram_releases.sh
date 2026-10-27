#!/usr/bin/env bash
# Safe targeted deployment to the *existing* modern Skryon Telegram bot.
# This does not publish releases, modify Nginx, or restart skryon-api/VPN services.
set -Eeuo pipefail
umask 077
repo=/opt/nt
runtime="/opt/nt/emery vpn orchestrator"
python=/opt/nt/.runtime/modern/bin/python
service=skryon-bot.service
ref=origin/skryon-rel-review-1027
path1=src/bot/handlers/windows_releases.py
path2=src/bot/utils/windows_release_store.py
old_handler_blob=a22e8150036413c98d1cbb70078c2646ce84a6ca
old_store_blob=94496a196652e82e252b9484b8c5fe05d426cd88
candidate_handler_blob=2b856b77756b9cc4ae48f298d78bd8c0959e11242
candidate_store_blob=14165005b6ab2039350233cf8421d54d605b67d6
[[ -x "$python" && -f "$runtime/$path1" && -f "$runtime/$path2" ]] || {
  echo "REFUSING: unexpected bot layout"; exit 31; }
systemctl is-active --quiet "$service" || {
  echo "REFUSING: existing bot not active"; exit 32; }
systemctl cat "$service" | grep -Fq "src.bot.main" || {
  echo "REFUSING: not the expected modern bot"; exit 33; }
[[ "$(git -C "$repo" hash-object "$runtime/$path1")" == "$old_handler_blob" ]] || {
  echo "REFUSING: live bot handler differs from verified baseline"; exit 34; }
[[ "$(git -C "$repo" hash-object "$runtime/$path2")" == "$old_store_blob" ]] || {
  echo "REFUSING: live release store differs from verified baseline"; exit 35; }
work="$(mktemp -d /tmp/skryon-release-deploy.XXXXXXXX)"
git -C "$repo" show "$ref:emery vpn orchestrator/$path1" > "$work/handler.py"
git -C "$repo" show "$ref:emery vpn orchestrator/$path2" > "$work/store.py"
[[ "$(git -C "$repo" hash-object "$work/handler.py")" == "$candidate_handler_blob" ]] || {
  echo "REFUSING: wrong handler candidate"; exit 36; }
[[ "$(git -C "$repo" hash-object "$work/store.py")" == "$candidate_store_blob" ]] || {
  echo "REFUSING: wrong store candidate"; exit 37; }
"$python" -m py_compile "$work/handler.py" "$work/store.py"
backup="/opt/skryon-bot-admin-backups/offline-signed-release-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
cp -p "$runtime/$path1" "$backup/windows_releases.py"
cp -p "$runtime/$path2" "$backup/windows_release_store.py"
changed=0
restore() {
    code=$?
    trap - ERR
    if [[ "$changed" == 1 ]]; then
        cp -p "$backup/windows_releases.py" "$runtime/$path1"
        cp -p "$backup/windows_release_store.py" "$runtime/$path2"
        systemctl restart "$service" || true
        echo "ROLLED_BACK_BOT_CHANGES"
    fi
    exit "$code"
}
trap restore ERR
changed=1
install -m 0644 "$work/handler.py" "$runtime/$path1"
install -m 0644 "$work/store.py" "$runtime/$path2"
(cd "$runtime" && "$python" -c 'from src.bot.handlers.windows_releases import router; from src.bot.utils import windows_release_store as store; assert router.name == "windows_releases"; assert store.code_for("1.0.1") == 20261027; print("IMPORT_AND_VERSION_CHECK_OK")')
systemctl restart "$service"
sleep 3
systemctl is-active --quiet "$service"
pid="$(systemctl show "$service" -p MainPID --value)"
[[ "$pid" =~ ^[1-9][0-9]*$ ]]
changed=0
trap - ERR
echo "TELEGRAM_RELEASE_ROUTES_INSTALLED"
echo "BOT_PID=$pid"
echo "ROLLBACK_BACKUP=$backup"
echo "API_AND_VPN_UNCHANGED"
echo "RELEASE_NOT_PUBLISHED"
(cd "$runtime" && "$python" -c 'from src.bot.utils import windows_release_store as s; print("READINESS_GAPS=" + "; ".join(s.publishing_readiness()))')
