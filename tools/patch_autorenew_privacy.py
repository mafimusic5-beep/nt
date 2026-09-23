#!/usr/bin/env python3
from pathlib import Path
import re
import shutil
import sys
from datetime import datetime, timezone

root = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/www/skryon.ru/html")
marker = "SKRYON_AUTORENEW_PRIVACY_V1"

patterns = (
    "privacy*.html",
    "policy*.html",
    "politic*.html",
    "confidential*.html",
    "personal-data*.html",
    "personal_data*.html",
)

candidates = []
for pattern in patterns:
    candidates.extend(root.glob(pattern))

# Also inspect small HTML files whose visible title clearly says this is a privacy policy.
for path in root.glob("*.html"):
    if path in candidates:
        continue
    try:
        if path.stat().st_size > 500_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    head = text[:15_000].lower()
    if "политика конфиденциальности" in head or "обработк" in head and "персональн" in head:
        candidates.append(path)

unique = []
seen = set()
for path in candidates:
    resolved = str(path.resolve())
    if resolved not in seen and path.is_file():
        unique.append(path)
        seen.add(resolved)

if not unique:
    print("NO_EXISTING_PRIVACY_FILE: dedicated /autorenewal terms will be used")
    raise SystemExit(0)
if len(unique) > 1:
    print("MULTIPLE_PRIVACY_FILES_SKIP:")
    for path in unique:
        print(path)
    raise SystemExit(0)

path = unique[0]
s = path.read_text(encoding="utf-8")
if marker in s:
    print(f"AUTORENEW_PRIVACY_ALREADY_PATCHED file={path}")
    raise SystemExit(0)

section = f'''
<section id="autorenew-privacy" data-skryon-marker="{marker}">
  <h2>Автопродление и платежные данные</h2>
  <p>Автопродление является дополнительной опцией и подключается только по явному выбору пользователя. Для проведения повторных платежей могут обрабатываться адрес электронной почты, технический идентификатор сохраненного способа оплаты ЮKassa, тариф, период, сумма платежа, дата и версия согласия, а также идентификаторы и статусы платежных операций.</p>
  <p>Полные реквизиты банковской карты, включая полный номер карты и CVC/CVV, обрабатываются платежной инфраструктурой ЮKassa и не хранятся Skryon. Технический идентификатор способа оплаты используется исключительно для инициирования согласованных повторных платежей и управления автопродлением.</p>
  <p>Пользователь может отключить автопродление до следующего успешного списания через персональную ссылку управления или обратившись в поддержку. После отключения технический идентификатор перестает использоваться для новых автоматических списаний. Сведения о совершенных платежах и согласии могут храниться в объеме и в течение сроков, необходимых для учета, разрешения споров и соблюдения обязательных требований.</p>
  <p><a href="/autorenewal">Условия автопродления Skryon</a>.</p>
</section>
'''

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(path.name + f".bak-autorenew-privacy-{stamp}")
shutil.copy2(path, backup)

if "</main>" in s:
    s = s.replace("</main>", section + "\n</main>", 1)
elif "</body>" in s:
    s = s.replace("</body>", section + "\n</body>", 1)
else:
    raise SystemExit(f"privacy page has no safe insertion anchor; backup={backup}; nothing written")

if marker not in s or "/autorenewal" not in s:
    raise SystemExit(f"privacy validation failed; backup={backup}; nothing written")

path.write_text(s, encoding="utf-8")
print(f"AUTORENEW_PRIVACY_PATCH_OK file={path} backup={backup}")
