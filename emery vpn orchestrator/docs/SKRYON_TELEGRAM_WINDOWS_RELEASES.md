# Skryon bot: Windows releases and admin panel

## Status

This is a **staged feature branch**. Deploy it only on the host actually running
the active `src.bot.main` process. Do not deploy another process with the same
Telegram token on the web host: simultaneous polling causes conflicts.

The web host is **31.70.117.82**. The live VPN API was observed on
**31.70.76.155**. Access to the bot's live machine is required before these
handlers appear in Telegram.

## Owner experience

Click **👑 Админ → 📦 Релизы Windows** and send the latest
`SkryonSetup-1.0.1.exe` file to the bot as a DOCUMENT in a PRIVATE chat.
The bot reads the embedded Windows ProductVersion, computes SHA-256, verifies
Authenticode with `osslsigncode`, stages the file and displays an approval
button. Publishing requires a separate second confirmation.

The owner also gets **🖥 Управление VPS**, **➕ Добавить VPS** (guided prompts,
password deleted immediately after receipt), node list, disable server,
clear assignment slots with explicit confirmations, stats, keys and search.

Non-admins cannot access these callbacks or upload binaries.

## Большие Windows EXE прямо в чате Telegram: без API ID / API Hash

Обычный Telegram Bot API позволяет скачивать части файлов не более 20 MB.
Сборка Skryon сейчас весит около 105 MB. Решение: нарезать оригинальный
`SkryonSetup-1.0.0.exe` на файлы **по 18 MiB**, используя PowerShell-скрипт
`Documents/Skryon.Windows/tools/Prepare-Telegram-Release.ps1` на Windows ПК
разработчика, затем отправить **все .skpart части как документы** в личный чат
владельца с действующим ботом.

- Кнопка **👑 Админ → 📦 Релизы Windows → 📥 Загрузить** объясняет процесс.
- Можно отправить части в любом порядке, за один раз или по отдельности.
- Бот скачивает каждую часть через обычный `getFile`, сохраняет её в
  закрытую папку, сообщает прогресс N/M, продолжает после перезапуска.
- После последней части собирает EXE побайтово, сверяет полный SHA-256 из
  строгого имени части, проверяет фактические PE-метаданные версии и
  выводит карточку сборки.
- Повторную часть с несовпадающими байтами бот не примет, а при ошибке
  итогового хеша забракует весь набор.
- Незаконченные части можно удалить кнопкой «Сбросить незавершённые части».
- Для EXE до 18 MiB допускается обычная единая загрузка.
- В стандартном режиме **никакие Telegram API ID и Hash не нужны**, и
  не надо менять polling действующего бота.

### Тестирование реальной сборки

В изолированной тестовой среде реальный установщик 1.0.0 (~105 MB)
был собран из шести частей, прошёл SHA-256, PE ProductVersion и запись
в очередь проверки. 22 теста отдельных релизных модулей пройдены.

**Важно:** загрузка и публикация являются РАЗНЫМИ действиями.
Система публикации по-прежнему требует Authenticode, подписанный
ECDSA-манифест, правильный ключ издателя и ограниченный SFTP.
Обход проверок не разрешён.

## Secure release publisher requirements

Install a dedicated SSH/SFTP upload account on IONOS static site server.
Constrain it to static release directories and deny shell login where possible.
Pre-create `/var/www/skryon.ru/html/downloads/windows/` and
`/var/www/skryon.ru/html/updates/windows/` with the correct ownership.

Required environment variables on the BOT host only:

- `SKRYON_WINDOWS_RELEASE_DIR` = private root writable by bot
- `SKRYON_RELEASE_SFTP_HOST` = IONOS web hostname/IP
- `SKRYON_RELEASE_SFTP_USER` = restricted release publisher username
- `SKRYON_RELEASE_SFTP_PRIVATE_KEY` = private Ed25519 key FILE path, chmod 600
- `SKRYON_RELEASE_SFTP_HOSTKEY_SHA256` = verified, pinned SSH host public key fingerprint
- `SKRYON_RELEASE_SITE_ROOT` = `/var/www/skryon.ru/html` or a chroot-correct path
- `SKRYON_RELEASE_SIGNING_KEY_FILE` = ECDSA P-256 PEM signing key FILE path, chmod 600
- `SKRYON_RELEASE_CLIENT_PUBLIC_SPKI_B64` = **EXACT** base64 SPKI key embedded in installed Windows clients
- optional `SKRYON_RELEASE_SIGNING_KEY_PASSWORD` if the key file is encrypted

**IMPORTANT publisher-key migration:**
The initial Windows client currently trusts an ECDSA update-signing key whose
private half is NONEXPORTABLE on the developer's Windows PC in the CNG store.
That key is NOT on the bot host, intentionally. Therefore this bot *cannot
automatically sign* a manifest trusted by the old client until you either:

- ship a signed bootstrap release through the existing developer-side signing
  tool that adds a new dedicated bot-held update publisher public key to the
  next client, THEN move release publication to the bot-managed publisher key; or
- implement a secure offline owner signing approval path (e.g. send the manifest
  to a trusted owner signer, then return the signature to the bot).

**Never export/upload the existing CNG publisher private key, and never disable
client verification to make deployment easier.** The bot refuses publish
while the configured signing key and expected client SPKI disagree.

## Safety gates

- Bot does not execute uploads and does not trust Telegram file names.
- Version is extracted from PE metadata using `pefile`, but this alone does
  NOT prove the bundled Skryon app's compiled protocol version; both must
  come from the same signed release build pipeline.
- Bot refuses release publishing if Authenticode verification is missing/failed.
  Run `osslsigncode` with a complete CA trust store on the bot host.
- Release data are validated with SHA-256 and signed ECDSA P-256.
- SFTP host is pinned and the remote bytes are checked before publishing.
  The `latest.json` pointer is moved last.
- Loading a release does **NOT** raise the API's minimum supported version.
  Mandatory version changes remain DISABLED pending a separately audited
  server endpoint with rollback and verification of the published download.
  Existing Android users are unaffected.
- If SFTP upload succeeds but writing state fails, inspect the website;
  do not retry blindly. Keep staging until reconciliation.

## Onboarding and tests

Run with an isolated test bot token on staging or a maintenance window:

```bash
pip install -r requirements.txt
apt-get install osslsigncode
python -m pytest -q tests/test_windows_release_admin.py
```

Test 1.0.0 / 1.0.1 PE parsing, 105 MB document upload, private admin
authorization, site file SHA256, signed update manifest acceptance on Windows,
rejected unsigned installer, restart/resume, and a failed SFTP upload.
Do not switch the old bot to local Bot API until this test passes.
