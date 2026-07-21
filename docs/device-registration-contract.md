# Device registration contract

The Android client treats device registration as successful only after two independent signed backend responses agree. The backend remains the source of truth and must enforce the tariff limit inside a database transaction.

## Tariff limits

| Canonical tariff | Maximum active devices |
| --- | ---: |
| `Личный` / `Personal` | 1 |
| `Личный+` / `Personal Plus` | 2 |
| `Семейный` / `Family` | 5 |

The limit must come from the server-side tariff record. Never accept a limit supplied by the Android request.

## Device identity

Every Android installation generates an EC key pair in Android Keystore. The private key is non-exportable. Requests contain:

- `device_id`;
- `device_name`;
- `client_public_key` during initial registration;
- timestamp and single-use nonce;
- ECDSA SHA-256 signature;
- app version and platform.

The backend must bind a device row to both the normalized `device_id` and the stored public-key fingerprint. A matching ID with a different key is not the same device and must be rejected.

## `POST /api/activate`

This endpoint currently delivers the VLESS configuration used by the Premium activation screen. It must not return a configuration merely because the code is valid.

Before returning `config`, the endpoint must either:

1. invoke the same atomic registration operation described for `/auth/key`; or
2. require a previously committed active device row whose ID and public-key fingerprint match the signed request.

It must verify the timestamp, nonce and ECDSA proof itself or through a shared registration service. When the tariff limit is reached it must return HTTP `409` with `device_limit_reached` and no configuration. This server-side check is required because a modified client could otherwise call `/api/activate` directly and bypass Android-side validation.

## `POST /auth/key`

The endpoint must perform the following work in one database transaction:

1. Validate the access key, expiry, ban state and tariff.
2. Reject timestamps outside the permitted clock window.
3. Consume the nonce using a unique constraint so it cannot be replayed.
4. Verify the ECDSA signature over the canonical request payload.
5. Lock the access-key or subscription row (`SELECT ... FOR UPDATE`, advisory lock, or equivalent).
6. Find an active device by key fingerprint and `device_id`.
7. If the device is new, count active device rows while the lock is held.
8. If the count is already at the tariff limit, roll back and return HTTP `409` with `device_limit_reached`.
9. Otherwise insert or reactivate the device row and update `last_seen_at`.
10. Re-read the active count and complete device inventory in the same transaction.
11. Commit and return the response below.

Concurrent registration requests must serialize on the same subscription. A separate `COUNT` followed by an unlocked `INSERT` is not sufficient because two devices could pass the limit simultaneously.

Required success fields:

```json
{
  "valid": true,
  "device_registered": true,
  "device_id": "same-id-from-signed-request",
  "device_name": "Pixel 7",
  "plan_name": "Личный+",
  "devices_used": 2,
  "devices_limit": 2,
  "vpn_enabled": true,
  "router_enabled": false,
  "expires_at": "2026-12-31T23:59:59Z",
  "devices": [
    {
      "device_id": "...",
      "device_name": "Pixel 7",
      "platform": "android",
      "app_version": "1.0.0",
      "first_seen_at": "2026-07-21T12:00:00Z",
      "last_seen_at": "2026-07-21T20:00:00Z",
      "active": true,
      "is_current": true
    }
  ]
}
```

## `GET /profile`

This request is signed with the already registered device key. The backend must:

1. Validate the access key and request proof.
2. Require an active device row whose ID and stored key fingerprint match the signature.
3. Update `last_seen_at`.
4. Return the same `device_id`, tariff, exact active count, limit and complete inventory.

The Android client rejects the response when:

- the current device is absent or inactive;
- a `device_id` appears more than once;
- the active row count differs from `devices_used`;
- the limit is not exactly 1, 2 or 5 for the named tariff;
- either response reports different counters or a different current device.

## Database constraints

Recommended minimum constraints:

```sql
UNIQUE (subscription_id, device_id)
UNIQUE (subscription_id, public_key_fingerprint)
UNIQUE (subscription_id, nonce)
```

Device removal should normally mark a row inactive rather than delete audit history. Re-activation must go through the same locked limit check.

## Configuration delivery

All VPN configuration and refresh endpoints must require the same signed registered-device proof. A valid access code by itself must not be enough to download or refresh a VPN configuration.
