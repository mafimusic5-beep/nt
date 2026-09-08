#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

DOMAIN="${SKRYON_DOMAIN:-skryon.ru}"
TLS_PORT="${SKRYON_ORIGIN_TLS_PORT:-2053}"
LEGACY_UPSTREAM="${SKRYON_LEGACY_UPSTREAM:-http://127.0.0.1:8080}"
MODERN_UPSTREAM="${SKRYON_MODERN_UPSTREAM:-http://127.0.0.1:9330}"
CONF_AVAILABLE=/etc/nginx/sites-available/skryon-control-origin
CONF_ENABLED=/etc/nginx/sites-enabled/skryon-control-origin
TLS_DIR=/etc/skryon/origin-tls
CERT="$TLS_DIR/origin.crt"
KEY="$TLS_DIR/origin.key"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run_as_root_required" >&2
  exit 1
fi

command -v nginx >/dev/null 2>&1 || {
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y nginx openssl
}
command -v openssl >/dev/null 2>&1 || {
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y openssl
}

mkdir -p "$TLS_DIR"
chmod 700 "$TLS_DIR"

# Keep Xray on :443 untouched. Cloudflare connects to this dedicated HTTPS
# origin port through an Origin Rule. A self-signed origin certificate is
# acceptable with Cloudflare SSL/TLS mode Full. Replace it with an Origin CA or
# publicly trusted certificate before switching to Full (Strict).
if [[ ! -s "$CERT" || ! -s "$KEY" ]]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes \
    -days 825 \
    -subj "/CN=$DOMAIN" \
    -addext "subjectAltName=DNS:$DOMAIN,DNS:www.$DOMAIN" \
    -keyout "$KEY" \
    -out "$CERT"
fi
chmod 600 "$KEY"
chmod 644 "$CERT"

cat > "$CONF_AVAILABLE" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    listen ${TLS_PORT} ssl;
    listen [::]:${TLS_PORT} ssl;

    server_name ${DOMAIN} www.${DOMAIN};

    ssl_certificate ${CERT};
    ssl_certificate_key ${KEY};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SKRYON:10m;
    ssl_session_timeout 1d;

    client_max_body_size 2m;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy no-referrer always;

    # Modern orchestrator API. This route must never fall through to the legacy
    # 8080 service, otherwise endpoints such as /api/v1/vpn/connect return 404.
    location ^~ /api/v1/ {
        proxy_pass ${MODERN_UPSTREAM};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }

    # Activation, config sync, device profile/device-gate compatibility routes.
    location / {
        proxy_pass ${LEGACY_UPSTREAM};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
EOF

ln -sfn "$CONF_AVAILABLE" "$CONF_ENABLED"
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl enable nginx.service
systemctl restart nginx.service
systemctl is-active --quiet nginx.service

curl -fsS --max-time 5 -H "Host: $DOMAIN" http://127.0.0.1/health >/dev/null
curl -kfsS --max-time 5 --resolve "$DOMAIN:$TLS_PORT:127.0.0.1" "https://$DOMAIN:$TLS_PORT/health" >/dev/null
curl -kfsS --max-time 5 --resolve "$DOMAIN:$TLS_PORT:127.0.0.1" "https://$DOMAIN:$TLS_PORT/api/v1/health" >/dev/null

echo "skryon_origin=ready"
echo "domain=$DOMAIN"
echo "origin_tls_port=$TLS_PORT"
echo "cloudflare_required=SSL/TLS Full + Origin Rule destination port $TLS_PORT"
echo "cloudflare_strict=replace $CERT/$KEY with trusted or Cloudflare Origin CA certificate first"
