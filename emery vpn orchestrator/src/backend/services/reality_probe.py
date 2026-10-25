"""Pre-publication REALITY verification for /setup_server.

The probe uses the newly installed Xray binary and a temporary localhost SOCKS
client to test actual VLESS+REALITY TLS and HTTP egress, not just systemctl.
It never changes host routing, stores persistent credentials, or exposes a port
outside 127.0.0.1.
"""


def reality_probe_shell() -> str:
    return r"""
# Re-derive the published REALITY password from the server private key.
DERIVED_KEYS="$(xray x25519 -i "$PRIVATE_KEY")"
DERIVED_PUBLIC="$(printf '%s\n' "$DERIVED_KEYS" | awk -F': *' '/^Public key:|^Password:|^Password \(PublicKey\):/{print $2; exit}')"
if [[ -z "$DERIVED_PUBLIC" || "$DERIVED_PUBLIC" != "$PUBLIC_KEY" ]]; then
  echo 'reality_public_key_mismatch' >&2
  exit 78
fi
unset DERIVED_KEYS DERIVED_PUBLIC

PROBE_DIR="$(mktemp -d)"
chmod 0700 "$PROBE_DIR"
PROBE_PID=""
probe_cleanup() {
  if [[ -n "$PROBE_PID" ]]; then
    kill "$PROBE_PID" >/dev/null 2>&1 || true
    wait "$PROBE_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$PROBE_DIR"
}
trap probe_cleanup EXIT
PROBE_PORT="$(python3 - <<'PY_PORT'
import socket
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY_PORT
)"
cat >"$PROBE_DIR/client.json" <<EOF_PROBE
{
  "log": {"loglevel": "error"},
  "inbounds": [{
    "tag": "probe-socks",
    "listen": "127.0.0.1",
    "port": $PROBE_PORT,
    "protocol": "socks",
    "settings": {"auth": "noauth", "udp": false}
  }],
  "outbounds": [{
    "tag": "probe-vless",
    "protocol": "vless",
    "settings": {"vnext": [{
      "address": "127.0.0.1",
      "port": $XRAY_LISTEN_PORT,
      "users": [{"id": "$UUID", "encryption": "none", "flow": "xtls-rprx-vision"}]
    }]},
    "streamSettings": {
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "serverName": "$REALITY_SNI",
        "fingerprint": "chrome",
        "publicKey": "$PUBLIC_KEY",
        "shortId": "$SHORT_ID",
        "spiderX": "/"
      }
    }
  }]
}
EOF_PROBE
chmod 0600 "$PROBE_DIR/client.json"
if ! xray run -test -config "$PROBE_DIR/client.json" >/dev/null 2>&1; then
  echo 'reality_probe_client_config_invalid' >&2
  exit 78
fi
xray run -config "$PROBE_DIR/client.json" >"$PROBE_DIR/client.log" 2>&1 &
PROBE_PID="$!"
PROBE_OK=""
for _ in 1 2 3; do
  kill -0 "$PROBE_PID" >/dev/null 2>&1 || break
  if curl --noproxy '' -fsS --socks5-hostname "127.0.0.1:$PROBE_PORT" \
    --connect-timeout 4 --max-time 10 -o /dev/null \
    https://skryon.ru/robots.txt 2>/dev/null; then
    PROBE_OK=1
    break
  fi
  sleep 1
done
if [[ "$PROBE_OK" != 1 ]]; then
  echo 'reality_data_plane_probe_failed' >&2
  exit 78
fi
probe_cleanup
trap - EXIT
printf 'XRAY_REALITY_VERIFIED=1\n'
"""
