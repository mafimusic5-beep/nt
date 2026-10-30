# SKRYON engineering contract for AI coding agents

Applies to the entire repository. Treat these rules as release blockers, not optional suggestions.

## First: understand the whole user journey

Before editing code, inspect callers, implementations, adjacent consumers, tests, and deployment paths. Trace the affected flow end-to-end:
- Android: button/Compose -> permission -> access/device binding -> region/policy -> session API -> config import -> VpnService/TUN -> Xray/device gate -> traffic verification -> Connected UI/timer/notification -> disconnect/reconnect/release.
- Activation and billing: website -> checkout -> YooKassa authoritative payment status -> idempotent subscription activation -> activation code -> Telegram bot and Android device.
- Infrastructure: site/frontend -> orchestrator/backend -> VPN node. Desktop administration is not part of the live connection path.

For each requested behavior, identify neighboring features that **must continue working**. A feature is not fixed if another one silently disappears.

## Mandatory change protocol

1. **Baseline**: state the bug, exact reproduction, current behavior, expected behavior, and uncertainty. Check existing failing CI against the base branch; do not attribute unrelated failures to the patch.
2. **Impact map**: inspect every caller/callee and all relevant state transitions and background jobs, not just the function mentioned. Explicitly identify concurrency, Android lifecycle, persisted preferences, retries, auth, session cleanup, and API response contracts.
3. **Preserve contracts**: do not remove existing user-facing behavior to fix latency or reliability. Make it non-blocking, move it to an appropriate stage, or replace it with equivalent UX. Document any intentional product-behavior change.
4. **Small patch**: prefer narrow, reversible edits. Do not refactor unrelated components, change payment/security rules, rotate credentials, modify live servers, or touch DNS because of an Android symptom without evidence.
5. **Regression checks**: add/update a test for the reported failure AND a test for each nearby behavior likely to break. Run Android build + unit tests; run backend/API tests if contracts change; include integration/device tests for VPN on real Android. Tests must assert behavior, not just that the code compiles.
6. **Review the actual diff**: scan removed code for silently lost features; check cancellation, race conditions, double requests, session leaks, error UI, permission denial, cold start, reconnect, and offline mode.
7. **Gate the release**: commit to a feature branch and open a draft PR first. Never merge/release if required checks are red or unverified. Report what was proven versus merely hypothesized. Do not claim a fix works without testing on an actual device when Android platform behavior is involved.
8. **Rollback plan**: ensure changes can be reverted, state any backward compatibility requirements, and preserve working subscription/activation data.

## Non-negotiable invariants

- One tap starts an Android VPN attempt immediately. Optional speed tests, diagnostics, alerts, and notifications must not gate startup or require a second tap. Preserve useful weak-network warnings as non-blocking UX.
- Show “Connected”/“Защита включена” and run connection timer only after evidence that VPN tunnel traffic works. Starting a foreground service or displaying a speed notification alone does not prove connectivity.
- VPN connect/disconnect must be idempotent enough to tolerate Android lifecycle events and repeated taps. Never allow a background config sync to replace the selected profile during an active connect/session. Bound retry loops, timeouts, and polling intervals.
- Preserve Android VPN permission handling, device-bound identity/proof, `protect()`, regional routing, and session release. Avoid exposing credentials, full activation codes, payment tokens, or private keys in logs or PR text.
- Payment handlers must validate authoritative provider state and be idempotent. Never let a Telegram notification failure affect the correctness of an already confirmed subscription.
- Protect the existing production website, database, domain registration, Purelymail MX/TXT, and VPN clients during changes. Never deploy or cancel an old server without an explicit migration/rollback assessment.
- A red GitHub Actions run is a **stop**, including known baseline failures. Distinguish and disclose those failures; do not silently bypass tests or declare the patch “ready”.

## Before finishing a coding response

State: changed files; old feature(s) preserved; tests run and results; what is untested; CI status (including unrelated failures); production/merge status; exact next validation step. Never equate “APK compiled” with “VPN works on Android”.
