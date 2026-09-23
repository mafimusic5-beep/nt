#!/usr/bin/env python3
from pathlib import Path
import re
import shutil
import sys
from datetime import datetime, timezone

path = Path(sys.argv[1] if len(sys.argv) > 1 else '/var/www/skryon.ru/html/checkout.html')
s = path.read_text(encoding='utf-8')

if 'SKRYON_RENEW_DIRECT_V2' in s:
    print('UI_ALREADY_PATCHED')
    raise SystemExit(0)
if 'SKRYON_RENEWAL_UI_WORKING_V1' not in s:
    raise SystemExit('expected renewal UI marker is missing; nothing changed')

stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup = path.with_name(path.name + f'.bak-renew-ui-{stamp}')
shutil.copy2(path, backup)

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
    raise SystemExit(f'panel patch count={n}; backup={backup}')

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
    raise SystemExit(f'mode switch anchor missing; backup={backup}')
s = s.replace(old_mode, new_mode, 1)

listener_pattern = re.compile(r"\n  renewCheck\.addEventListener\('click', async \(\) => \{.*?\n  \}\);\n", re.S)
s, n = listener_pattern.subn('\n', s, count=1)
if n != 1:
    raise SystemExit(f'check listener patch count={n}; backup={backup}')

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
    raise SystemExit(f'payment validation anchor missing; backup={backup}')
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

if 'id="sk-renew-check-new"' in s or "/api/checkout/find-code" in s:
    raise SystemExit(f'public lookup remnants remain; backup={backup}')
if 'Тариф определяется по вашему коду' not in s:
    raise SystemExit(f'server-plan note missing; backup={backup}')

path.write_text(s, encoding='utf-8')
print(f'UI_PATCH_OK backup={backup}')
