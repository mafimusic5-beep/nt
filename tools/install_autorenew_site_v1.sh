#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/var/www/skryon.ru/html
BASE=https://raw.githubusercontent.com/mafimusic5-beep/nt/main
STAGE=$(mktemp -d /tmp/skryon-autorenew-site.XXXXXX)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
PAGE_BACKUP=""

cleanup() { rm -rf "$STAGE"; }
rollback() {
  rc=$?
  trap - ERR
  if [[ -n "$PAGE_BACKUP" && -f "$PAGE_BACKUP" ]]; then
    cp -a "$PAGE_BACKUP" "$ROOT/autorenewal.html" || true
  fi
  cleanup
  exit "$rc"
}
trap rollback ERR
trap cleanup EXIT

[[ -f "$ROOT/checkout.html" ]]

curl -fsSL "$BASE/tools/patch_checkout_autorenew_ui.py" -o "$STAGE/patch_checkout_autorenew_ui.py"
curl -fsSL "$BASE/tools/patch_autorenew_privacy.py" -o "$STAGE/patch_autorenew_privacy.py"
curl -fsSL "$BASE/tools/autorenewal.html" -o "$STAGE/autorenewal.html"
python3 -m py_compile "$STAGE/patch_checkout_autorenew_ui.py" "$STAGE/patch_autorenew_privacy.py"
grep -q 'Автопродление Skryon' "$STAGE/autorenewal.html"

if [[ -f "$ROOT/autorenewal.html" ]]; then
  PAGE_BACKUP="$ROOT/autorenewal.html.bak-$STAMP"
  cp -a "$ROOT/autorenewal.html" "$PAGE_BACKUP"
fi

python3 "$STAGE/patch_checkout_autorenew_ui.py" "$ROOT/checkout.html"
install -o www-data -g www-data -m 0644 "$STAGE/autorenewal.html" "$ROOT/autorenewal.html"
mkdir -p "$ROOT/autorenewal"
install -o www-data -g www-data -m 0644 "$STAGE/autorenewal.html" "$ROOT/autorenewal/index.html"
python3 "$STAGE/patch_autorenew_privacy.py" "$ROOT"

nginx -t
grep -q 'SKRYON_AUTORENEW_UI_V1' "$ROOT/checkout.html"
grep -q 'Автопродление Skryon' "$ROOT/autorenewal.html"
if grep -q '/api/checkout/find-code' "$ROOT/checkout.html"; then
  echo 'STOP: old public code lookup returned to checkout' >&2
  exit 81
fi

echo 'SITE_AUTORENEW_POLICY_OK'
echo "Terms: $ROOT/autorenewal.html and $ROOT/autorenewal/index.html"
