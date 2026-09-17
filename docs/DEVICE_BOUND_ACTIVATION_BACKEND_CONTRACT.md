# Device-bound activation contract for Emery

This Android patch changes the client-side activation flow from "plain key validation" to "key + device proof".

## Activation request

`POST /auth/key`

Expected JSON body:

```json
{
  "key": "EVPN-XXXX-XXXX",
  "access_key": "EVPN-XXXX-XXXX",
  "device_id": "stable-installation-uuid",
  "device_name": "Android-устройство",
  "client_public_key": "BASE64_X509_P256_PUBLIC_KEY",
  "timestamp": "1711999999999",
  "nonce": "random_nonce",
  "signature": "BASE64_ECDSA_SIGNATURE",
  "signature_algorithm": "SHA256withECDSA",
  "client_platform": "android",
  "app_version": "2.0.15"
}
```

The same values are also sent in headers:

- `X-Emery-Device-Id`
- `X-Emery-Timestamp`
- `X-Emery-Nonce`
- `X-Emery-Signature`
- `X-Emery-Signature-Algorithm`

## Activation signature payload

The Android client signs this canonical string with an EC P-256 key from Android Keystore:

```text
method=POST
path=/auth/key
device_id=<device_id>
device_name=<device_name>
timestamp=<timestamp>
nonce=<nonce>
auth_sha256=<sha256(access_key)>
```

## Secure follow-up requests

The client now adds the same device-bound headers to these authenticated requests:

- `GET /profile`
- `POST /api/vpn/session/acquire`
- `POST /api/vpn/session/heartbeat`
- `POST /api/vpn/session/release`

Canonical string for signed follow-up requests:

```text
method=<HTTP_METHOD>
path=<PATH_ONLY>
device_id=<device_id>
timestamp=<timestamp>
nonce=<nonce>
auth_sha256=<sha256(bearer_access_key)>
```

## Backend validation rules

1. Verify the access key exists and was actually issued by your bot/backend.
2. Enforce the plan window on the server side:
   - 1 month
   - 3 months
   - 6 months
   - 12 months
3. Bind every activated device to:
   - `device_id`
   - `client_public_key`
4. Allow unlimited registered installations per access key. Enforce the tariff only as 1/2/5 simultaneous VPN session leases.
5. Reject replays using:
   - `timestamp`
   - `nonce`
6. Verify ECDSA signature with the stored public key for every authorized request after activation.

## Suggested activation response

```json
{
  "valid": true,
  "vpn_enabled": true,
  "router_enabled": false,
  "expires_at": "2027-04-01T00:00:00Z",
  "plan_name": "12 months",
  "device_id": "stable-installation-uuid",
  "device_name": "Android-устройство",
  "devices_used": 2,
  "devices_limit": 5
}
```

## Suggested error codes

- `invalid_or_expired_key`
- `concurrent_limit_reached`
- `device_signature_invalid`
- `device_not_registered`
- `device_mismatch`
- `bad_request`

## Session semantics

Registration never consumes a paid slot. `/api/vpn/session/acquire` atomically admits a short-lived VPN lease only when the number of non-expired leases is below the tariff limit. The same installation can refresh its current lease without counting twice. `/api/vpn/session/heartbeat` extends the lease while the VPN foreground service is alive, and `/api/vpn/session/release` frees it on a normal disconnect. A crashed client is released automatically when the lease expires.
