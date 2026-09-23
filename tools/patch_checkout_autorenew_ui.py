#!/usr/bin/env python3
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

path = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/www/skryon.ru/html/checkout.html")
s = path.read_text(encoding="utf-8")

if "SKRYON_AUTORENEW_UI_V1" in s:
    print("AUTORENEW_UI_ALREADY_PATCHED")
    raise SystemExit(0)
if "SKRYON_RENEW_DIRECT_V2" not in s:
    raise SystemExit("expected SKRYON_RENEW_DIRECT_V2 marker missing; nothing changed")
if "</body>" not in s:
    raise SystemExit("closing body tag missing; nothing changed")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(path.name + f".bak-autorenew-ui-{stamp}")
shutil.copy2(path, backup)

snippet = r'''
<!-- SKRYON_AUTORENEW_UI_V1 -->
<style id="sk-autorenew-style-v1">
  #sk-autorenew-box{display:none;margin:16px 0;padding:16px;border:1px solid rgba(120,140,128,.28);border-radius:16px;background:rgba(120,160,132,.07)}
  #sk-autorenew-box.sk-ready{display:block}
  #sk-autorenew-box label{display:flex;gap:11px;align-items:flex-start;cursor:pointer;font-weight:650}
  #sk-autorenew-box input{width:19px;height:19px;margin-top:2px;flex:0 0 auto}
  #sk-autorenew-note{margin:9px 0 0 30px;font-size:13px;line-height:1.45;opacity:.76}
  #sk-autorenew-note a{color:inherit;text-decoration:underline;text-underline-offset:2px}
</style>
<script>
(() => {
  const nativeFetch = window.fetch.bind(window);
  let toggle = null;

  function findPayButton() {
    const direct = document.querySelector('#sk-pay, #pay, [data-sk-pay], button[type="submit"]');
    if (direct) return direct;
    return [...document.querySelectorAll('button')].find((button) =>
      /перейти\s+к\s+оплате|продлить\s+подписку|оплатить/i.test(button.textContent || '')
    ) || null;
  }

  function mountBox() {
    if (document.getElementById('sk-autorenew-box')) {
      toggle = document.getElementById('sk-autorenew-toggle');
      return document.getElementById('sk-autorenew-box');
    }
    const payButton = findPayButton();
    if (!payButton || !payButton.parentNode) return null;
    const box = document.createElement('div');
    box.id = 'sk-autorenew-box';
    box.innerHTML = `
      <label for="sk-autorenew-toggle">
        <input id="sk-autorenew-toggle" type="checkbox" autocomplete="off">
        <span>Включить автопродление</span>
      </label>
      <div id="sk-autorenew-note">
        Повторный платеж — за 24 часа до окончания оплаченного периода, на тот же срок и на сумму, зафиксированную при подключении. Платежный способ сохраняется в ЮKassa. Skryon не хранит полные реквизиты карты. Можно отключить в любой момент. <a href="/autorenewal" target="_blank" rel="noopener">Условия автопродления</a>.
      </div>`;
    payButton.parentNode.insertBefore(box, payButton);
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
    const box = mountBox();
    if (!box) return;
    try {
      const response = await nativeFetch('/api/checkout/payment-ready', {cache:'no-store'});
      const data = await response.json();
      if (response.ok && data && data.autorenew === true) {
        box.classList.add('sk-ready');
      }
    } catch (_) {}
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

if s.count("SKRYON_AUTORENEW_UI_V1") != 1:
    raise SystemExit(f"autorenew marker validation failed; backup={backup}")
if "/api/checkout/find-code" in s:
    raise SystemExit(f"old public code lookup unexpectedly present; backup={backup}")
if "autorenewConsent" not in s or "/autorenewal" not in s:
    raise SystemExit(f"autorenew UI validation failed; backup={backup}")

path.write_text(s, encoding="utf-8")
print(f"AUTORENEW_UI_PATCH_OK backup={backup}")
