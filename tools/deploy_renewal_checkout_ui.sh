#!/usr/bin/env bash
set -euo pipefail

mkdir -p ~/.ssh
printf '%s\n' "$VPS_SSH_PRIVATE_KEY" | tr -d '\r' > ~/.ssh/id_ed25519
chmod 600 ~/.ssh/id_ed25519
ssh-keygen -y -f ~/.ssh/id_ed25519 >/dev/null

user="${VPS_USER:-root}"
site_host=''
site_port=''
seen=' '
for h in 157.22.206.113 "${VPS_HOST:-}"; do
  [ -n "$h" ] || continue
  h="${h#http://}"; h="${h#https://}"; h="${h%%/*}"; h="${h%%:*}"
  for p in 22 8443 2022 2222 2200 22222 8022; do
    key=" $h:$p "
    [[ "$seen" != *"$key"* ]] || continue
    seen+="$key"
    if ssh -4 -p "$p" -i ~/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new "$user@$h" true >/dev/null 2>&1; then
      site_host="$h"
      site_port="$p"
      break 2
    fi
  done
done
[ -n "$site_host" ] || { echo 'Site VPS did not accept deploy key'; exit 1; }

ssh -4 -p "$site_port" -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new "$user@$site_host" 'bash -s' <<'REMOTE'
set -euo pipefail
file=/var/www/skryon.ru/html/checkout.html
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
cp -a "$file" "$file.bak-renew-ui-$stamp"

python3 - <<'PY'
from pathlib import Path
import re

p = Path('/var/www/skryon.ru/html/checkout.html')
s = p.read_text(encoding='utf-8')

if 'SKRYON_RENEW_DIRECT_V2' in s:
    print('UI_ALREADY_PATCHED')
    raise SystemExit(0)
if 'SKRYON_RENEWAL_UI_WORKING_V1' not in s:
    raise SystemExit('expected renewal UI marker is missing')

s = s.replace(
    "// SKRYON_RENEWAL_UI_WORKING_V1\n  let checkoutMode = 'new';\n  let verifiedRenewCode = '';",
    "// SKRYON_RENEWAL_UI_WORKING_V1\n  // SKRYON_RENEW_DIRECT_V2\n  let checkoutMode = 'new';",
    1,
)

panel_pattern = re.compile(r"  renewUI\.innerHTML = `.*?`;\n\n  const emailBlock =", re.S)
panel_new = '''  renewUI.innerHTML = `
    <div class="sk-renew-ui-title">Подписка</div>

    <div class="sk-renew-ui-tabs">
      <button type="button" class="sk-renew-ui-tab active" data-sk-mode="new">Новая подписка</button>
      <button type="button" class="sk-renew-ui-tab" data-sk-mode="renew">Продлить подписку</button>
    </div>

    <div class="sk-renew-ui-panel" id="sk-renew-panel-new" hidden>
      <input id="sk-renew-code-new" type="text" autocomplete="off" spellcheck="false" placeholder="Введите код Skryon">
      <div id="sk-renew-info-new" aria-live="polite">Тариф определяется по вашему коду и при продлении не изменяется.</div>
    </div>
  `;

  const emailBlock ='''
s, n = panel_pattern.subn(panel_new, s, count=1)
if n != 1:
    raise SystemExit(f'panel patch count={n}')

s = re.sub(r"\n  const renewCheck =\n    renewUI\.querySelector\('#sk-renew-check-new'\);\n", '\n', s, count=1)
s = re.sub(
    r"\n  function resetVerifiedRenewCode\(\) \{.*?\n  renewInput\.addEventListener\(\n    'input',\n    resetVerifiedRenewCode\n  \);\n",
    '\n', s, count=1, flags=re.S,
)

old_mode = """        renewPanel.hidden =
          checkoutMode !== 'renew';

        resetVerifiedRenewCode();

        if (checkoutMode === 'renew') {
          renewInput.focus();
        }"""
new_mode = """        renewPanel.hidden =
          checkoutMode !== 'renew';

        const planSection =
          document.querySelector('#sk-plans')?.closest('.sk-section');

        if (planSection) {
          planSection.hidden = checkoutMode === 'renew';
        }

        renewInfo.textContent =
          checkoutMode === 'renew'
            ? 'Тариф определяется по вашему коду и при продлении не изменяется.'
            : '';

        if (checkoutMode === 'renew') {
          renewInput.focus();
          pay.textContent = 'Продлить подписку';
        } else {
          pay.textContent = 'Перейти к оплате';
        }"""
if old_mode not in s:
    raise SystemExit('mode switch anchor missing')
s = s.replace(old_mode, new_mode, 1)

listener_pattern = re.compile(r"\n  renewCheck\.addEventListener\('click', async \(\) => \{.*?\n  \}\);\n", re.S)
s, n = listener_pattern.subn('\n', s, count=1)
if n != 1:
    raise SystemExit(f'check listener patch count={n}')

old_validation = """    let renewalCode = '';

    if (checkoutMode === 'renew') {
      if (!verifiedRenewCode) {
        renewInput.focus();

        message(
          'Сначала проверьте код Skryon.',
          true
        );

        return;
      }

      renewalCode = verifiedRenewCode;
    }"""
new_validation = """    let renewalCode = '';

    if (checkoutMode === 'renew') {
      renewalCode = renewInput.value.trim();

      if (!renewalCode) {
        renewInput.focus();
        message('Введите код Skryon.', true);
        return;
      }
    }"""
if old_validation not in s:
    raise SystemExit('payment validation anchor missing')
s = s.replace(old_validation, new_validation, 1)

if "renew_code_invalid:" not in s:
    s = s.replace(
        "          invalid_months:\n            'Выберите срок 1, 3, 6 или 12 месяцев.',",
        "          invalid_months:\n            'Выберите срок 1, 3, 6 или 12 месяцев.',\n          renew_code_invalid:\n            'Не удалось продлить этот код. Проверьте код и попробуйте ещё раз.',",
        1,
    )

s = s.replace(
    "      pay.textContent = 'Перейти к оплате';\n      message(\n        e?.message || 'Ошибка соединения. Попробуйте ещё раз.',",
    "      pay.textContent = checkoutMode === 'renew'\n        ? 'Продлить подписку'\n        : 'Перейти к оплате';\n      message(\n        e?.message || 'Ошибка соединения. Попробуйте ещё раз.',",
    1,
)

p.write_text(s, encoding='utf-8')
print('UI_PATCH_OK')
PY

chown www-data:www-data "$file"
chmod 0644 "$file"
nginx -t
grep -q 'SKRYON_RENEW_DIRECT_V2' "$file"
! grep -q 'id="sk-renew-check-new"' "$file"
! grep -q "fetch('/api/checkout/find-code'" "$file"
grep -q 'Тариф определяется по вашему коду' "$file"
REMOTE

html=$(curl -fsSL --max-time 15 "https://skryon.ru/checkout?renew-ui=$(date +%s)")
grep -q 'SKRYON_RENEW_DIRECT_V2' <<<"$html"
! grep -q 'id="sk-renew-check-new"' <<<"$html"
! grep -q "fetch('/api/checkout/find-code'" <<<"$html"
grep -q 'Тариф определяется по вашему коду' <<<"$html"
echo 'Renewal checkout UI verified publicly.'
