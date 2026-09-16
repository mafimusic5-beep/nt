# Concurrent VPN admission — code/CI complete, production rollout pending

Status: opt-in implementation validated in CI. Do not enable on production yet.
The current `main` installation-count behavior remains the default until the
real-device and production-topology checks below are completed.

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
- Tariff downgrade is enforced on existing live sessions. After 5→2, 5→1 or
  2→1, logical sessions above the new limit cannot renew.
- Pool allocation is separated from registration. At most the paid 1/2/5 Xray
  resources remain owned for a code. An existing resource may be rebound only
  from an installation with no live lease; rebind preserves its Xray UUID/port
  and does not increment node capacity.
- Downgrade reconciliation releases excess offline Xray assignments and decrements
  node capacity. Live assignments are never selected for physical release.
- Release is expected-owner and idempotent. A local `release_pending_*` claim
  prevents config sync or another resource transfer from racing the remote Xray
  removal. Ambiguous network failures keep the claim for retry across cleanup
  cycles/restarts; only an explicit owner-change response restores the prior
  local status.
- Revoked assignments are retained for audit/idempotency while their positive
  client port is retired from the uniqueness key, allowing the real TCP port to
  be reused safely.
- `limit_mode=concurrent` separates online count from the registered inventory.
  A valid subscription with zero online devices is supported by Android.
- Android preserves `concurrent_limit_reached` from config sync and presents a
  specific message instead of collapsing it into the generic sync/start error.

The feature adds no connection-history table. Existing subscription, device,
audit, web-server and backup storage is not removed by this patch. Do not claim
that the whole service is anonymous or log-free on the basis of this feature.

## Validation completed

GitHub Actions validation is green for the crash-safe implementation:

- full orchestrator test suite;
- full modern backend test suite;
- Alembic upgrade → downgrade → upgrade migration cycle;
- downgrade/resource-release tests, including live-owner protection, stale-owner
  rejection, retry after removal failure, idempotent release and freed-port reuse;
- Android `:app:assemblePlaystoreDebug` with Java 17;
- signed Android protocol version check for versionCode 718 / versionName 2.0.18.

CI validates code and build integration. It does not prove real-device behavior
or that production ingress/firewall topology is configured correctly.

## Remaining blockers before production enablement

1. Run real-device Android cases: first startup, idle VPN, screen-off/Doze,
   mobile↔Wi-Fi handover, reconnect, abrupt process/network loss, graceful stop,
   1/2/5 limit exhaustion and recovery when another device disconnects.
2. Verify production topology: every public ingress must be gated; raw VLESS
   ports must remain loopback/firewall restricted. All gates must use one shared
   admission authority/database rather than independent SQLite copies.
3. Coordinate rollout of authority, all gates and Android version. Enable
   `CONCURRENT_SESSIONS_ENABLED` and `EMERY_GATE_CONCURRENT_SESSIONS` only after
   the components above are deployed and checked together.
4. Review admin/inventory wording and retention semantics. Registered devices
   remain durable inventory records even though enforcement is based on concurrent
   sessions; administrative screens should not describe inventory count as the
   online limit.

## Configuration for isolated tests / coordinated rollout

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

## Local validation

Run the two Python projects separately because they use different pytest import
roots, then build Android:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=orchestrator python -m pytest -q orchestrator/tests
cd 'emery vpn orchestrator' && python -m pytest -q && python -m alembic upgrade head
./gradlew :app:assemblePlaystoreDebug --no-daemon --stacktrace
```

The automated tests exercise signed admission, 1/2/5 limits, multi-stream
counting, concurrent workers, shared limits across nodes, release/expiry,
revocation, reinstall without eviction, downgrade resource cleanup, Xray release
failure recovery and forwarding cancellation. They do not prove production
deployment or Android runtime behavior on physical devices.
