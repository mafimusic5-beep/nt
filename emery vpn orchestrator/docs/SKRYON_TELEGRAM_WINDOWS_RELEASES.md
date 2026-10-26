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

## Large files: local Telegram Bot API server is REQUIRED

Cloud `getFile` is limited to 20 MB download, while Skryon Windows is 105+ MB.
The production bot must be switched to a LOCAL Bot API server
(`telegram-bot-api` with `--local`) before owner-uploaded EXEs will work.

1. Create `api_id` and `api_hash` in the official Telegram developer portal.
   Treat them as secrets. **Do not put them in code, GitHub or chat.**
2. Provision a service `telegram-bot-api` on the SAME bot host, bound to
   127.0.0.1:8081, with a private work directory and an unprivileged account.
   Launch with `--api-id=... --api-hash=... --http-port=8081 --local`.
   Never expose this local API endpoint to the public Internet.
3. Move the existing bot token from the cloud endpoint to the local API using
   the officially documented `logOut` operation; use a controlled maintenance
   window to avoid long-polling conflicts. Stop the old bot first. Restart it
   with `SKRYON_TELEGRAM_LOCAL_API_URL=http://127.0.0.1:8081`.
   If the first file upload fails, do NOT enable mandatory updates.
4. Ensure bot process can read the absolute `file_path` supplied by the local
   API server, or configure both to use a shared, private file directory.

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
