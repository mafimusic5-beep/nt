# Device-bound VLESS gateway

This listener makes a copied `vless://` string insufficient for access. Every
new public TCP connection must prove possession of the ECDSA private key held
by the registered Android device. Xray's per-device inbound remains bound to
`127.0.0.1`, so it cannot be reached directly from the Internet.

## Required deployment order

1. Give every VPN node a DNS name and a valid publicly trusted TLS certificate.
2. Create a locked system account named `emery-gate`. Put
   `emery_device_gate.py` in `/opt/emery/device-gate/` and install the example
   systemd unit as `emery-device-gate.service`. Make the certificate chain and
   private key readable by that account (for example, root-owned with the
   `emery-gate` group and mode `0640`); never make the key world-readable.
3. Create `/etc/emery/device-gate.env` owned by root with mode `0600`. Use a
   distinct random `EMERY_GATE_AUTHORIZE_KEY`; set the same value as
   `DEVICE_GATE_API_KEY` on the activation orchestrator. Pin the public key of
   the leaf TLS certificate (not the certificate fingerprint) and put the
   lowercase digest in `EMERY_GATE_SPKI_SHA256`:

   ```bash
   openssl x509 -in /etc/letsencrypt/live/gate.example.com/fullchain.pem -pubkey -noout \
     | openssl pkey -pubin -outform DER \
     | openssl dgst -sha256
   ```

   Copy only the 64 hexadecimal characters after `=`.
4. Allow the public gate port through the firewall. Keep the Xray assignment
   port range blocked publicly; each generated inbound also binds to loopback.
5. Verify the gate service is active on every configured node. Set each node's
   `device_gate_host`, `device_gate_port`, `device_gate_server_name`, and
   `device_gate_spki_sha256`; the pin must equal the value in that node's gate
   environment file. Existing nodes can be updated through
   `PUT /api/v1/admin/nodes/{node_id}/device-gate` before gate mode is enabled.
6. Deploy Android version code 718 or newer. Only then enable
   `DEVICE_BOUND_GATE_ENABLED=true` together with the existing pool safety
   feature flags.
7. Immediately run the authenticated pool maintenance endpoint and require
   `failed=0`. It rewrites every pre-gate active assignment to loopback and
   marks it protected. A second run should report `migrated=0`. Keep the whole
   assignment port range blocked by the public firewall throughout rollout.

Activation fails closed while the gate is disabled, misconfigured, or not
running. Never put the authorize key in the Android application or in a VLESS
URI. A TLS certificate key rotation changes the SPKI pin: update the node and
gateway values together, then refresh affected assignments before enabling the
new certificate.
