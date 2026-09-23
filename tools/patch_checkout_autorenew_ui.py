#!/usr/bin/env python3
from pathlib import Path
import re
import shutil
import sys
from datetime import datetime, timezone

path = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/www/skryon.ru/html/checkout.html")
s = path.read_text(encoding="utf-8")

if "SKRYON_AUTORENEW_UI_V2" in s:
    print("AUTORENEW_UI_V2_ALREADY_PATCHED")
    raise SystemExit(0)
if "SKRYON_RENEW_DIRECT_V2" not in s:
    raise SystemExit("expected SKRYON_RENEW_DIRECT_V2 marker missing; nothing changed")
if "</body>" not in s:
    raise SystemExit("closing body tag missing; nothing changed")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(path.name + f".bak-autorenew-ui-v2-{stamp}")
shutil.copy2(path, backup)

# Remove the previous autorenew UI block if it was installed.
s = re.sub(
    r'\n?<!-- SKRYON_AUTORENEW_UI_V1 -->\s*<style id="sk-autorenew-style-v1">.*?</style>\s*<script>.*?</script>\s*',
    "\n",
    s,
    count=1,
    flags=re.S,
)

snippet = r'''
<!-- SKRYON_AUTORENEW_UI_V2 -->
<style id="sk-autorenew-style-v2">
  .sk-email-label-v2{display:block;margin:0 0 8px;font-weight:650;font-size:14px;line-height:1.3}
  .sk-email-hint-v2{margin:7px 0 0;font-size:12px;line-height:1.4;opacity:.68}
  #sk-autorenew-box{margin:14px 0 16px;padding:14px 15px;border:1px solid rgba(120,140,128,.28);border-radius:16px;background:rgba(120,160,132,.07)}
  #sk-autorenew-box label{display:flex;gap:11px;align-items:flex-start;cursor:pointer;font-weight:650;line-height:1.35}
  #sk-autorenew-box input{width:19px;height:19px;margin:1px 0 0;flex:0 0 auto}
  #sk-autorenew-box.is-disabled label{cursor:default;opacity:.58}
  #sk-autorenew-note{margin:8px 0 0 30px;font-size:12px;line-height:1.45;opacity:.72}
  #sk-autorenew-note a{color:inherit;text-decoration:underline;text-underline-offset:2px}
  #sk-autorenew-state{margin:7px 0 0 30px;font-size:12px;line-height:1.35;opacity:.68}
</style>
<script>
(() => {
  const nativeFetch = window.fetch.bind(window);
  let toggle = null;

  function findEmailInput() {
    return document.querySelector(
      'input[type="email"], input[autocomplete="email"], input[name="email"], #email, #sk-email'
    );
  }

  function normalizeEmailField() {
    const input = findEmailInput();
    if (!input || !input.parentNode) return null;

    if (!input.id) input.id = 'sk-checkout-email';

    let label = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
    if (!label) {
      label = [...document.querySelectorAll('label, .label, .field-label, p, span, div')]
        .find((el) => {
          if (el === input || el.contains(input)) return false;
          const text = (el.textContent || '').trim().replace(/\s+/g, ' ');
          return /^(электронная\s+почта|e-?mail)(\s|$|:)/i.test(text) && text.length < 90;
        }) || null;
    }

    if (!label) {
      label = document.createElement('label');
      label.textContent = 'Электронная почта';
    }
    label.classList.add('sk-email-label-v2');
    label.setAttribute('for', input.id);
    input.parentNode.insertBefore(label, input);

    let hint = input.parentNode.querySelector('.sk-email-hint-v2');
    if (!hint) {
      hint = document.createElement('div');
      hint.className = 'sk-email-hint-v2';
      hint.textContent = 'На эту почту придёт подтверждение оплаты и информация по подписке.';
      input.insertAdjacentElement('afterend', hint);
    }
    return {input, host: input.parentNode, hint};
  }

  function findPayButton() {
    const direct = document.querySelector('#sk-pay, #pay, [data-sk-pay], button[type="submit"]');
    if (direct) return direct;
    return [...document.querySelectorAll('button')].find((button) =>
      /перейти\s+к\s+оплате|продлить\s+подписку|оплатить/i.test(button.textContent || '')
    ) || null;
  }

  function mountAutorenew() {
    const email = normalizeEmailField();
    if (document.getElementById('sk-autorenew-box')) {
      toggle = document.getElementById('sk-autorenew-toggle');
      return document.getElementById('sk-autorenew-box');
    }

    const box = document.createElement('div');
    box.id = 'sk-autorenew-box';
    box.className = 'is-disabled';
    box.innerHTML = `
      <label for="sk-autorenew-toggle">
        <input id="sk-autorenew-toggle" type="checkbox" autocomplete="off" disabled>
        <span>Автопродление подписки</span>
      </label>
      <div id="sk-autorenew-note">
        Списание выполняется перед окончанием оплаченного периода. Платёжный способ сохраняется в ЮKassa, полные реквизиты карты Skryon не хранит. <a href="/autorenewal" target="_blank" rel="noopener">Условия</a>.
      </div>
      <div id="sk-autorenew-state">Проверяем доступность автопродления…</div>`;

    const payButton = findPayButton();
    if (email && email.host) {
      email.host.insertAdjacentElement('afterend', box);
    } else if (payButton && payButton.parentNode) {
      payButton.parentNode.insertBefore(box, payButton);
    } else {
      return null;
    }

    toggle = box.querySelector('#sk-autorenew-toggle');
    return box;
  }

  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const options = init ? {...init} : {};
    const method = String(options.method || 'GET').toUpperCase();
    if (method === 'POST' && url.includes('/api/checkout/payment') && options.body) {
      try {
        const data = JSON.parse(options.body);
        const enabled = Boolean(toggle && toggle.checked && !toggle.disabled);
        data.autorenew = enabled;
        data.autorenewConsent = enabled;
        options.body = JSON.stringify(data);
      } catch (_) {}
    }
    return nativeFetch(input, options);
  };

  async function initAutorenew() {
    const box = mountAutorenew();
    if (!box) return;
    const state = box.querySelector('#sk-autorenew-state');
    try {
      const response = await nativeFetch('/api/checkout/payment-ready', {cache:'no-store'});
      const data = await response.json();
      if (response.ok && data && data.autorenew === true) {
        box.classList.remove('is-disabled');
        toggle.disabled = false;
        state.textContent = 'Опция выключена по умолчанию. Включите её только если хотите автоматические списания.';
      } else {
        box.classList.add('is-disabled');
        toggle.disabled = true;
        state.textContent = 'Автопродление сейчас недоступно. Разовая оплата работает как обычно.';
      }
    } catch (_) {
      box.classList.add('is-disabled');
      toggle.disabled = true;
      state.textContent = 'Автопродление сейчас недоступно. Разовая оплата работает как обычно.';
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAutorenew, {once:true});
  } else {
    initAutorenew();
  }
})();
</script>
'''

s = s.replace("</body>", snippet + "\n</body>", 1)

if s.count("SKRYON_AUTORENEW_UI_V2") != 1:
    raise SystemExit(f"autorenew v2 marker validation failed; backup={backup}")
if "/api/checkout/find-code" in s:
    raise SystemExit(f"old public code lookup unexpectedly present; backup={backup}")
if "autorenewConsent" not in s or "/autorenewal" not in s:
    raise SystemExit(f"autorenew v2 validation failed; backup={backup}")

path.write_text(s, encoding="utf-8")
print(f"AUTORENEW_UI_V2_PATCH_OK backup={backup}")
