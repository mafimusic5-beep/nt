#!/usr/bin/env python3
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

root = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/www/skryon.ru/html")
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

PRIVACY_MARKER = "SKRYON_AUTORENEW_PRIVACY_V2"
OFFER_MARKER = "SKRYON_AUTORENEW_OFFER_V2"

privacy = root / "privacy.html"
offer = root / "offer.html"

for path in (privacy, offer):
    if not path.is_file():
        raise SystemExit(f"required legal page missing: {path}; nothing changed")

privacy_section = f'''
<section id="autorenew-privacy" data-skryon-marker="{PRIVACY_MARKER}">
  <h2>Автопродление и платежные данные</h2>
  <p>Автопродление является дополнительной опцией и подключается только по явному выбору пользователя. Для работы автопродления могут обрабатываться адрес электронной почты, технический идентификатор сохраненного способа оплаты ЮKassa, выбранный тариф и период, сумма платежа, дата и версия согласия, а также идентификаторы и статусы платежных операций.</p>
  <p>Полные реквизиты банковской карты, включая полный номер карты и CVC/CVV, обрабатываются платежной инфраструктурой ЮKassa и не хранятся Skryon. Skryon использует технический идентификатор сохраненного способа оплаты для инициирования согласованных повторных платежей и управления автопродлением.</p>
  <p>Пользователь может отключить автопродление до следующего успешного списания через персональную защищенную ссылку управления либо обратившись в поддержку. После отключения сохраненный платежный идентификатор не используется Skryon для новых автоматических списаний. Сведения об уже проведенных платежах и зафиксированном согласии могут сохраняться в объеме и в течение сроков, необходимых для учета, разрешения споров и исполнения обязательных требований.</p>
  <p><a href="/autorenewal">Условия автопродления Skryon</a>.</p>
</section>
'''

offer_section = f'''
<section id="autorenew-offer" data-skryon-marker="{OFFER_MARKER}">
  <h2>Автоматическое продление подписки</h2>
  <p>Автопродление не подключается автоматически. Пользователь подключает его отдельно, отмечая соответствующую галочку при оплате и подтверждая условия повторных списаний.</p>
  <p>При подключении автопродления платежный способ сохраняется на стороне ЮKassa. Период следующего продления соответствует периоду, выбранному при подключении автопродления: 1, 3, 6 или 12 месяцев. Сумма повторного платежа фиксируется в момент подключения автопродления и не увеличивается автоматически без нового согласия пользователя.</p>
  <p>Первая попытка повторного списания может выполняться примерно за 24 часа до окончания оплаченного периода. Если платеж отклонен, Skryon может выполнить до четырех повторных попыток с интервалом около 6 часов. Подписка продлевается только после подтвержденного успешного платежа.</p>
  <p>Пользователь вправе отключить автопродление в любой момент до следующего успешного списания через персональную защищенную ссылку управления или через поддержку. Отключение автопродления не прекращает уже оплаченный период подписки.</p>
  <p>Подробные условия: <a href="/autorenewal">Автопродление Skryon</a>.</p>
</section>
'''


def prepare(path: Path, marker: str, section: str):
    source = path.read_text(encoding="utf-8")
    if marker in source:
        return source, False
    if "</main>" in source:
        updated = source.replace("</main>", section + "\n</main>", 1)
    elif "</body>" in source:
        updated = source.replace("</body>", section + "\n</body>", 1)
    else:
        raise SystemExit(f"safe insertion anchor missing in {path}; nothing changed")
    if marker not in updated or "/autorenewal" not in updated:
        raise SystemExit(f"validation failed for {path}; nothing changed")
    return updated, True

privacy_new, privacy_change = prepare(privacy, PRIVACY_MARKER, privacy_section)
offer_new, offer_change = prepare(offer, OFFER_MARKER, offer_section)

if not privacy_change and not offer_change:
    print("AUTORENEW_LEGAL_ALREADY_PATCHED")
    raise SystemExit(0)

backups = {}
try:
    for path, changed in ((privacy, privacy_change), (offer, offer_change)):
        if not changed:
            continue
        backup = path.with_name(path.name + f".bak-autorenew-legal-{stamp}")
        shutil.copy2(path, backup)
        backups[path] = backup

    if privacy_change:
        privacy.write_text(privacy_new, encoding="utf-8")
    if offer_change:
        offer.write_text(offer_new, encoding="utf-8")
except Exception:
    for path, backup in backups.items():
        try:
            shutil.copy2(backup, path)
        except Exception:
            pass
    raise

print(f"AUTORENEW_LEGAL_V2_OK privacy={privacy} offer={offer}")
for path, backup in backups.items():
    print(f"BACKUP {path.name}={backup}")
