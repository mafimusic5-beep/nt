# Concurrent VPN admission — draft, not production-ready

Status: opt-in implementation under test. Do not enable on production yet.
The current `main` installation-count behavior remains the default.

## Behavior implemented

- Personal / Personal+ / Family allow 1 / 2 / 5 simultaneous installation keys
  per activation code across all gates using the same authority database.
- Registration and reinstall do not consume an online place or evict another
  installation. Every transport authenticates its existing Keystore proof.
- One idle control connection holds the place while VPN is on. Multiple data
  streams from the same installation key share the place.
- Admission uses a SQLite write transaction, not a per-process counter.
- Each connection receives a random, gate-only lease token. The database stores
  its hash, existing device-row reference, key hash, assignment/node references,
  and expiry. No IP, hardware model, DNS, destination or traffic data is added.
- Gate renews every 15 seconds, closes streams on renewal failure, and stops
  before the 60-second authority expiry. It closes sockets before releasing.
  Last released transport frees the place. Lost messages expire within 60 seconds;
  cleanup runs every 30 seconds. Deletion is logical SQL deletion, not a promise
  of forensic erasure from SQLite pages, journals or existing backup snapshots.
- Key rotation, revocation, assignment change and subscription expiry reject
  renewals. An expired token cannot renew itself into an occupied place.
- `limit_mode=concurrent` separates online count from the registered inventory.
  A valid subscription with zero online devices is supported by Android.

The feature adds no connection-history table. Existing subscription, device,
audit, web-server and backup storage is not removed by this patch. Do not claim
that the whole service is anonymous or log-free on the basis of this feature.

## Blockers before merge or deployment

1. Pool allocation is still tied to installation registration. Removing the
   registration limit allows repeated installs to reserve additional Xray ports
   and node capacity. Redesign allocation to reserve at most the paid concurrent
   capacity, with safe reassignment only after the old lease is closed/expired;
   preserve regional policy and per-device speed limits. Do not enable this
   patch alone: it would regress capacity use despite correct admission limits.
2. Compile/test Android with the real SDK and dependencies. This environment
   could not download Gradle 9.4.1 (`Network is unreachable`); no APK was built.
   Exercise startup rejection, idle VPN, screen-off/Doze, mobile/Wi-Fi handover,
   reconnect and graceful shutdown on real devices. Surface limit-exhausted
   errors clearly; current gateway failure still uses the generic start error.
3. Verify production topology: every public ingress must be gated; raw VLESS
   ports must remain loopback/firewall restricted. All gates must use one shared
   admission authority/database (not independent SQLite copies).
4. Legacy renewal/admin paths still use registered-device counts. Update them
   to concurrent semantics, including tariff downgrade handling and inventory
   text. Existing installations remain durable records; review retention before
   calling unlimited registration a privacy improvement.
5. Version the new Android release and require it at rollout. Old clients only
   accept gateway protocol 1. The new app supports 1 and 2. Protocol 2 keeps the
   existing signed proof but adds a `session_only` framing field; that field
   confers no authorization and never enables data without a valid signature.

## Configuration for isolated tests only

Authority: `CONCURRENT_SESSIONS_ENABLED=true`.
All gates: `EMERY_GATE_CONCURRENT_SESSIONS=true`.
Both default to false. Authority in concurrent mode rejects old gates that do
not advertise `lease_protocol=1`; new gates require an authority lease response.
Do not mix modes during normal user traffic. Coordinate a maintenance window,
back up the databases, deploy/test all components, then enable together.
Rollback: stop the new gates and turn both flags off. Do not blindly restore the
old registration limit after new-mode use: inventories may exceed that limit.
The original application rollback commit is
`fc287bd2be171ec0a56ce90e1f37f97cc46ac403`; this branch starts at `e8506ba`.

## Validation

Run separately to avoid the two backend projects' different pytest import roots:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=orchestrator python -m pytest -q orchestrator/tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='emery vpn orchestrator' python -m pytest -q 'emery vpn orchestrator/tests/test_device_gate_gateway.py'
```

The tests exercise signed admission, 1/2/5 limits, multi-stream counting,
concurrent workers, shared limits across nodes, release/expiry, revocation,
reinstall without eviction, actual TCP control keepalive/closure and cancellation
of both forwarding tasks. They do not prove production deployment or Android
runtime behavior.
