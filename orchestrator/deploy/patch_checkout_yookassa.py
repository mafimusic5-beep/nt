#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timezone
import shutil

TARGET = Path("/var/www/skryon.ru/html/checkout.html")
MARKER = "SKRYON_YOOKASSA_CHECKOUT_V1"

PAYMENT_SCRIPT = r'''
<script id="SKRYON_YOOKASSA_CHECKOUT_V1">
document.addEventListener('DOMContentLoaded', function () {
  const sourcePay = document.querySelector('#sk-pay');
  const email = document.querySelector('#sk-email');
  const duration = document.querySelector('#sk-duration');
  const consent = document.querySelector('#pdConsent');
  const status = document.querySelector('#sk-status');
  if (!sourcePay || !email || !duration || !consent || !status) return;

  const pay = sourcePay.cloneNode(true);
  sourcePay.replaceWith(pay);
  pay.disabled = false;
  pay.textContent = 'Перейти к оплате';
  status.textContent = '';

  if (!document.querySelector('#sk-offer-acceptance')) {
    const note = document.createElement('div');
    note.id = 'sk-offer-acceptance';
    note.className = 'sk-email-help';
    note.style.margin = '0 2px 18px';
    note.innerHTML = 'Нажимая «Перейти к оплате», вы принимаете условия <a href="/offer.html" target="_blank" rel="noopener noreferrer">Публичной оферты</a>.';
    pay.parentNode.insertBefore(note, pay);
  }

  const planCodes = ['personal', 'personal_plus', 'family'];
  const monthValues = [1, 3, 6, 12];

  function selectedPlanCode() {
    const rows = Array.from(document.querySelectorAll('.sk-plan'));
    const index = rows.findIndex(row => row.classList.contains('selected'));
    return planCodes[index >= 0 ? index : 0];
  }

  function selectedMonths() {
    const index = Number(duration.value || 0);
    return monthValues[index] || 1;
  }

  function setError(message) {
    status.textContent = message;
    status.style.color = '#9c2f2f';
  }

  function setInfo(message) {
    status.textContent = message;
    status.style.color = '';
  }

  pay.addEventListener('click', async function () {
    const customerEmail = email.value.trim();
    if (!customerEmail || !email.checkValidity()) {
      email.focus();
      setError('Укажите корректную электронную почту.');
      return;
    }
    if (!consent.checked) {
      consent.focus();
      setError('Нужно подтвердить согласие на обработку персональных данных.');
      return;
    }

    pay.disabled = true;
    pay.textContent = 'Создаём платёж…';
    setInfo('Соединяемся с ЮKassa. Сумма повторно проверяется на сервере.');

    try {
      const response = await fetch('/api/checkout/payment', {
        method: 'POST',
        cache: 'no-store',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        },
        body: JSON.stringify({
          email: customerEmail,
          plan: selectedPlanCode(),
          months: selectedMonths(),
          personalDataConsent: true
        })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok || !data.confirmationUrl) {
        const messages = {
          personal_data_consent_required: 'Подтвердите согласие на обработку персональных данных.',
          invalid_email: 'Проверьте адрес электронной почты.',
          invalid_plan: 'Не удалось определить выбранный тариф.',
          invalid_months: 'Выберите срок 1, 3, 6 или 12 месяцев.',
          too_many_attempts: 'Слишком много попыток. Подождите несколько минут.',
          payment_not_configured: 'Оплата временно недоступна. Попробуйте немного позже.',
          payment_provider_unavailable: 'ЮKassa временно не отвечает. Попробуйте ещё раз.'
        };
        throw new Error(messages[data.reason] || 'Не удалось создать платёж. Попробуйте ещё раз.');
      }
      const target = new URL(data.confirmationUrl);
      if (target.protocol !== 'https:') throw new Error('Получена некорректная ссылка на оплату.');
      setInfo('Переходим на защищённую страницу ЮKassa…');
      location.assign(target.href);
    } catch (error) {
      pay.disabled = false;
      pay.textContent = 'Перейти к оплате';
      setError(error && error.message ? error.message : 'Ошибка соединения. Попробуйте ещё раз.');
    }
  });
});
</script>
'''.strip()


def main() -> None:
    if not TARGET.is_file():
        raise SystemExit(f"checkout not found: {TARGET}")
    source = TARGET.read_text(encoding="utf-8")
    changed = False

    # Keep the buyer email only for the current browser session, not indefinitely.
    updated = source.replace("localStorage.getItem(", "sessionStorage.getItem(")
    updated = updated.replace("localStorage.setItem(", "sessionStorage.setItem(")
    if updated != source:
        source = updated
        changed = True

    if MARKER not in source:
        marker = "</body>"
        if marker not in source:
            raise SystemExit("checkout has no </body> marker")
        source = source.replace(marker, PAYMENT_SCRIPT + "\n" + marker, 1)
        changed = True

    if not changed:
        print("checkout already patched")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = TARGET.with_name(f"checkout.html.before-yookassa.{stamp}")
    shutil.copy2(TARGET, backup)
    TARGET.write_text(source, encoding="utf-8")
    print(f"patched {TARGET}")
    print(f"backup {backup}")


if __name__ == "__main__":
    main()
