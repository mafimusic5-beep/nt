# Skryon: DPI exposure and anti-leak audit (26 October 2026)

**Status:** code review and protected-Windows-build offline tests only. Production
VPN endpoints, public certificates, server assignments, firewall and live client
routes have **not** been changed. Do **not** activate a VPN connection for
testing without the owner's explicit permission.

## Threat model

An ISP can observe a remote destination IP, port, DNS requests, TLS ClientHello
metadata including visible SNI where ECH is unavailable, connection sizes,
timing and duration. Encryption of payload or a browser-like Xray REALITY
fingerprint **does not make a VPN undetectable**. VPS IP classification and
traffic statistics remain possible even when protocol-level DPI is resisted.

### 1. External TLS gateway is currently the outer visible connection

Code path, not a claim about the currently configured production node:

- Windows `DeviceGateProxy.cs` uses `.NET SslStream` to the signed
  `eg_host:eg_port`, verifies public PKI trust **and** the expected SPKI.
- Android `DeviceBoundVlessProxy.kt` uses platform `SSLSocket` and SPKI pin.
- The remote Python gateway `emery_device_gate.py` is TLS and requires
  an ECDSA per-device challenge before forwarding to loopback-only Xray.
- The gateway's **default** listener port in source is `24443`. The
  current live node port has **not** been verified. If it is 24443, it is less
  common than HTTPS port 443 and easier to classify based on port alone.
- Xray's `REALITY + fingerprint=chrome` is **inside the outer gateway TLS**.
  The provider sees the outer SslStream/SSLSocket handshake, not the inner
  REALITY Chrome fingerprint. Setting `fingerprint=chrome` in the inner
  Xray configuration cannot by itself conceal the gateway connection.

**Priority architectural improvement (requires separate compatibility
release):** expose the device-proof gateway through an actual
standards-compliant HTTPS/WebSocket transport on **443**, with legitimate web
responses to ordinary HTTPS requests, real browser-compatible TLS and an
unchanged device identity challenge. Keep the legacy listener active while
testing a new parallel endpoint. Do **not** invent ALPN `h2` or `http/1.1`
unless the gateway really implements the advertised protocol. A random fake
HTTPS banner or renaming the domain does not solve TLS fingerprinting.

Before production rollout:

1. Determine whether 443 is in use on each VPN node. Do not steal the website,
   certificate or API listener.
2. Provision a valid dedicated HTTPS name and certificate; verify SPKI pins
   during rotation and enable only supported proxy/CDN modes. Ordinary CDN HTTP
   proxying cannot forward an arbitrary raw TCP gateway unmodified.
3. Add a new optional transport to server and both clients; maintain the
   device-bound challenge, signed proof, loopback-only Xray inbounds, per-device
   lease limits and fail-closed behavior. Refuse insecure fallback.
4. Test direct-IP/IPv6 leak, HTTP fallback, actual WSS frames,
   captive portals, reconnects, network switching, and bad/expired signatures.
5. Gradually opt in one test device. Keep rollback available and do not force
   mandatory updates before a verified signed client is live.

This redesign is **not implemented** in this PR.

### 2. Windows DNS and IPv6 leak precautions

Verified from the actual protected Windows binaries (test does not start VPN):

- sing-box TUN has IPv4 and IPv6 interface addresses.
- `auto_route=true`, `strict_route=true`, `dns_mode=hijack`.
- DNS uses HTTPS with `detour=skryon`.
- Routes ultimately use the authenticated local SOCKS5, and Xray's only
  outbound is VLESS/REALITY to loopback device gate.
- No plain direct Xray outbound fallback is permitted.
- The pinned, signed gateway IP is the intentional pre-tunnel route exclusion.

Regression guard added to local
`Documents/Skryon.Windows/tools/ProtectionSmoke/Program.cs`;
`PROTECTED_XRAY_TLS_REALITY_ONLY_OK` and
`PROTECTED_TUN_DNS_IPV6_STRICT_ROUTE_OK` passed on installed protected DLLs.
This tests configuration semantics. It is **not** a live packet capture and does
not prove absence of every OS/application leak.

### 3. Android IPv6 exposure fixed in this branch (not deployed)

Previously Android only added an IPv6 VPN address/route for Russia mode or
when `PREF_PREFER_IPV6` was enabled. On an IPv6-capable mobile network
outside those cases, IPv6-capable apps could bypass the VPN and reveal the
carrier IP despite an IPv4 VPN session.

This branch always installs IPv6 VPN routes independent of the preference.
Full-tunnel mode captures `::/0`; intentional LAN-bypass mode retains its
previous global unicast/FakeIPv6 routes. An unavailable IPv6 exit should fail
closed, not silently route through the user's native interface.

Files:
- `app/src/main/java/com/v2ray/ang/service/V2RayVpnService.kt`
- `app/src/main/java/com/v2ray/ang/service/SkryonTrafficPrivacyRoutes.kt`
- `app/src/test/java/com/v2ray/ang/service/SkryonTrafficPrivacyRoutesTest.kt`

CI is configured to assemble the Playstore debug variant and run the route
regression tests on the PR. Do not equate CI with physical ISP leak tests.

### 4. Remaining observable signals and limits

- The site's activation/update API over HTTPS and its DNS lookups occur
  before a VPN is connected. These may associate the app with Skryon,
  irrespective of tunnel camouflage.
- The public gateway domain, public certificate and cloud/VPS IP reputation
  remain observable without ECH or a genuinely HTTPS-compatible proxy edge.
- Persistent high-volume, long-lived connections remain distinguishable
  statistically. No protocol provides a meaningful 100% invisibility claim.
- Do not disable certificate verification, SPKI pinning, signed per-device proof
  or leak-prevention for the sake of an "anti-detect" indicator.

## Non-disruptive work performed

- Audited the Windows protected client (offline, VPN was off).
- Preserved and extended release-time configuration checks.
- Staged Android IPv6 capture fix and unit tests on a separate branch.
- Did not enable the VPN, restart production nodes or switch routes.

## Pre-release acceptance for a later HTTPS transport

1. Ordinary HTTPS clients receive legitimate HTTP responses on the same
   endpoint; browser fingerprints match a real, supported TLS/HTTP stack.
2. New clients successfully connect using the unchanged device-bound proof.
3. Old clients remain supported on the legacy endpoint until optional rollout
   completes.
4. Invalid signatures, revoked keys, mismatched TLS pins and expired leases
   are denied; raw VLESS is never public.
5. Provider-facing live packet capture (explicit test authorization required)
   checks port, SNI, TLS/ALPN, DNS, IPv4/IPv6 leakage and robustness.
