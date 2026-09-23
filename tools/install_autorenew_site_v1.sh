#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/var/www/skryon.ru/html
BASE=https://raw.githubusercontent.com/mafimusic5-beep/nt/main
STAGE=$(mktemp -d /tmp/skryon-autorenew-site.XXXXXX)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
PAGE_BACKUP=""
CHECKOUT_BACKUP=""

cleanup() { rm -rf "$STAGE"; }
rollback() {
  rc=$?
  trap - ERR
  if [[ -n "$CHECKOUT_BACKUP" && -f "$CHECKOUT_BACKUP" ]]; then
    cp -a "$CHECKOUT_BACKUP" "$ROOT/checkout.html" || true
  fi
  if [[ -n "$PAGE_BACKUP" && -f "$PAGE_BACKUP" ]]; then
    cp -a "$PAGE_BACKUP" "$ROOT/autorenewal.html" || true
  fi
  cleanup
  exit "$rc"
}
trap rollback ERR
trap cleanup EXIT

[[ -f "$ROOT/checkout.html" ]]
CHECKOUT_BACKUP="$ROOT/checkout.html.bak-autorenew-site-v2-$STAMP"
cp -a "$ROOT/checkout.html" "$CHECKOUT_BACKUP"

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
grep -q 'SKRYON_AUTORENEW_UI_V2' "$ROOT/checkout.html"
grep -q 'sk-email-label-v2' "$ROOT/checkout.html"
grep -q 'Автопродление подписки' "$ROOT/checkout.html"
grep -q 'Автопродление Skryon' "$ROOT/autorenewal.html"
if grep -q '/api/checkout/find-code' "$ROOT/checkout.html"; then
  echo 'STOP: old public code lookup returned to checkout' >&2
  exit 81
fi

echo 'SITE_AUTORENEW_UI_V2_OK'
echo "Checkout backup: $CHECKOUT_BACKUP"
echo "Terms: $ROOT/autorenewal.html and $ROOT/autorenewal/index.html"
