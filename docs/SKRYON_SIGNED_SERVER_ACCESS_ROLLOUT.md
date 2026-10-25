# Skryon registered-client server access (staged)

## Goal

The server should only deliver the VPN node catalogue and a short-lived
connection lease to a registered, paid device using the supported client
protocol. A public server-list request should not provide connection
credentials. The real VLESS/REALITY credentials remain behind
`/api/vpn/session/acquire`, device-key signatures, a verified subscription,
a concurrent lease, and gateway authorization.

**Threat-model limit:** An app version sent in an HTTP header, even when bound
into the per-device signature, is NOT proof of an unmodified Windows executable.
A modified process on the same signed-in machine may be able to ask the
registered key to sign. Device-bound keys and obfuscation raise the effort;
they do not make the desktop app invulnerable. Stronger app-origin assurances
require separate platform-supported attestation/signing, including Android
Play Integrity (bound to a server nonce), verified distribution/signing, and
the server refusing VPN use without a currently authorized session.

## API additions

* `GET /api/vpn/servers/authorized`: requires `Authorization: Bearer <access-code>`,
  `X-Emery-Device-Id`, `X-Emery-Timestamp`, `X-Emery-Nonce`,
  `X-Emery-Signature`, `X-Emery-Signature-Algorithm`,
  `X-Skryon-Proof-Version: 2`, and `X-Skryon-App-Version-Code`.
  Timestamp, ECDSA signature, registered public key, active subscription,
  and one-time nonce are verified against the same database as a VPN lease.
  Requests missing v2 or below the minimum version fail with HTTP 426.
* `POST /api/vpn/session/{acquire,heartbeat,release}`: accepts v2 signed
  version when both headers are present. Legacy v1 clients remain supported
  until all official clients have been upgraded.
* v2 canonical signed input extends the existing v1 canonical string
  **with exactly one final line**: `app_version_code=<number>`.
  Do not trust an unsigned version header as a substitute.

## Safe deployment sequence

1. Review and deploy this *backend code* on the ACTUAL API origin:
   the live IONOS web entrypoint currently proxies `/api/` to a separate
   backend at `31.70.76.155:8447`. Do **not** assume updating the website
   nginx proxy changes the backend. Back up the backend and data before rollout.
2. Default environment: `SKRYON_WINDOWS_MIN_VERSION_CODE=20261026`,
   `SKRYON_REQUIRE_VERSIONED_LEASES=0`,
   `SKRYON_PUBLIC_SERVER_LIST_DISABLED=0`, and
   `SKRYON_WINDOWS_APPROVED_VERSION_CODES=20261026` (update the allowlist
   before shipping each new build). Deploy backend FIRST and smoke
   test both Android v1 and Windows v2 against the API.
3. Distribute the new **official signed** Windows binary from an authenticated
   update channel. Current Windows build is not code-signed yet; obtain a
   signing certificate and verify the signature and update manifest on install.
4. Move Android to the authenticated v2 listing and v2 session protocol, or
   give Android a separately verified platform-attested equivalent.
5. Only after both supported clients migrate, turn on
   `SKRYON_REQUIRE_VERSIONED_LEASES=1` and
   `SKRYON_PUBLIC_SERVER_LIST_DISABLED=1`, in that order, with rollback
   and monitoring. Older clients will lose connectivity, as intended.
6. Keep revoked keys, concurrency limits, server-assigned VLESS credentials
   and short leases authoritative at the actual API + node gateway. Rotate
   credentials if an installation is reported compromised.

## Verification targets

* Missing v2/version or outdated version: 426 (fail closed).
* With a supported v2 claim, absent/invalid bearer or signature: 401/403.
* Signature for a different version/path: 401; replayed nonce: 401.
* Valid registered active device on the current version: receives node *metadata*.
* Wrong or expired subscription, revoked device, stolen code: no server list or
  session lease.
* Modified/extracted local executable: not cryptographically distinguishable
  without additional platform attestation; never claim otherwise.
* Run `orchestrator/tests/test_signed_server_catalog.py`, the existing device
  authentication test suite, the Windows WPF smoke suite, and end-to-end VPN
  probes before production enforcement.

**Current status:** The API backend at `31.70.76.155` was not writable using
the provided SSH access. Changes on this branch are a staged implementation,
not a live security gate. The Windows build is also staged and has not replaced
the installed program.
