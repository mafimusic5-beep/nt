# Skryon Windows: Telegram release pipeline (staging)

Status: **staged, not deployed or published** (2026-10-27).

The existing IONOS Telegram admin bot previously had /windows, "Загрузить Windows-сборку", and six .skpart file handling. This project has a locally tested patch to add release verification and an explicit "Выпустить обновление" approval. The user's Windows PC was asleep and authenticated server access was not available during this work.

Upload flow: private admin chat > /windows > Загрузить Windows-сборку > six .skpart documents > Проверить сборку > review build (e.g. 26K101), technical version, size and SHA-256 > Выпустить обновление. Upload never publishes anything automatically.

Security: Admin-only; private chat only; per-part/total size limits; chunk digest verification; PE resource inspection without executing the installer; fail closed when signer / trusted publisher is not configured; publication requires explicit approval and a signed manifest verified with the Windows client's existing public key. No changes to VPN session-gate minimum or customer connectivity are made by this code.

**Blocker for live publication:** verify the current IONOS production bot's /windows code, Windows ReleaseManifestTool signature format and key distribution, and deployed update endpoint. Do not invent a new manifest protocol or swap the app's verifier. A signed-feed publisher must atomically upload the file+manifest and return a verified receipt before users see it. The Windows EXE is currently unsigned (Authenticode) and requires distribution risk review.

See the downloadable source archive provided in the conversation for `orchestrator/windows_release_pipeline.py`, `orchestrator/windows_release_bot.py` and unit tests. Do not merge/deploy this branch without integrating it with live server code and a clean-machine update test.

Live production left unchanged.