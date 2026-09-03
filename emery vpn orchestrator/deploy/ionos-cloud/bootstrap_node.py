#!/usr/bin/env python3
"""Bootstrap ONLY a new, journalled IONOS Debian node; never purchases resources.

Invoked over pinned SSH by the controller. No IONOS token is present here.
Provider-side firewall and public DNS must already have been configured.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path("/opt/emery/ionos-bootstrap")
STATE = Path("/var/lib/emery-ionos")
XRAY_CONFIG = Path("/usr/local/etc/xray/config.json")
XRAY_BINARY = Path("/usr/local/bin/xray")
XRAY_ASSETS = Path("/usr/local/share/xray")
REGIONAL_STATE = Path("/var/lib/emery-regional-policy")
MAX_ARCHIVE = 128 * 1024 * 1024
CANARY_ID = 2147483000


class BootstrapError(RuntimeError):
    """Constant diagnostic codes only: no secrets or remote output."""


def safe_error(error: Exception) -> str:
    code = str(error) if isinstance(error, BootstrapError) else ""
    return code if re.fullmatch(r"bootstrap_[a-z0-9_]{1,100}", code) else type(error).__name__


def run(args: list[str], *, timeout: int = 60, data: str | None = None) -> str:
    try:
        result = subprocess.run(args, input=data, text=True, capture_output=True,
                                check=True, timeout=timeout, env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootstrapError("bootstrap_command_failed") from exc
    return result.stdout


def atomic(path: Path, content: str | bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, temporary = tempfile.mkstemp(prefix=".emery-ionos-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode() if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_object(path: Path, *, secret: bool = False) -> dict:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & (0o077 if secret else 0o022) or info.st_size > 65536):
        raise BootstrapError("bootstrap_unsafe_file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise BootstrapError("bootstrap_invalid_object")
    return value


def dns_name(value: str) -> str:
    if not isinstance(value, str) or len(value) > 253 or "." not in value or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in value.split(".")
    ):
        raise BootstrapError("bootstrap_invalid_hostname")
    return value


def validate(config: dict) -> None:
    if str(uuid.UUID(config["operation_id"])) != config["operation_id"]:
        raise BootstrapError("bootstrap_invalid_operation")
    if type(config["node_id"]) is not int or not 0 < config["node_id"] < 2147483647:
        raise BootstrapError("bootstrap_invalid_node")
    for key in ("endpoint", "management_ipv4"):
        address = ipaddress.ip_address(config[key])
        if address.version != 4 or not address.is_global:
            raise BootstrapError("bootstrap_public_ipv4_required")
    dns_name(config["hostname"])
    dns_name(config["reality_server_name"])
    if config["gate_port"] != 24443:
        raise BootstrapError("bootstrap_invalid_gate_port")
    start, end = config["assignment_port_start"], config["assignment_port_end"]
    if type(start) is not int or type(end) is not int or not 1024 <= start <= end <= 65535 or start <= 24443 <= end:
        raise BootstrapError("bootstrap_invalid_assignment_ports")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", config["authorize_key"]):
        raise BootstrapError("bootstrap_invalid_control_key")
    for key in ("authorize_url", "probe_url"):
        parsed = urllib.parse.urlsplit(config[key])
        if (parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username
                or parsed.password or parsed.query or parsed.fragment or any(ord(c) <= 32 for c in config[key])):
            raise BootstrapError("bootstrap_https_required")
        dns_name(parsed.hostname)
        if key == "authorize_url" and parsed.path != "/internal/device-gate/authorize":
            raise BootstrapError("bootstrap_control_path_invalid")
    if config["acme_terms_accepted"] is not True or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", config["acme_email"]):
        raise BootstrapError("bootstrap_acme_not_configured")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", config["xray_version"]):
        raise BootstrapError("bootstrap_xray_version_required")
    if not re.fullmatch(r"[a-f0-9]{64}", config["xray_sha256"]):
        raise BootstrapError("bootstrap_xray_checksum_required")


def install_packages() -> None:
    if run(["dpkg", "--print-architecture"]).strip() != "amd64":
        raise BootstrapError("bootstrap_amd64_required")
    run(["apt-get", "update"], timeout=300)
    run(["apt-get", "install", "-y", "--no-install-recommends", "ca-certificates", "curl",
         "nftables", "openssl", "certbot", "acl", "python3-cryptography"], timeout=900)


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if (parsed.scheme != "https" or parsed.hostname not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise BootstrapError("bootstrap_release_redirect_rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def extract_verified_xray(archive: Path, expected_sha256: str, destination: Path) -> None:
    checksum = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    if archive.stat().st_size > MAX_ARCHIVE or checksum.hexdigest() != expected_sha256:
        raise BootstrapError("bootstrap_xray_checksum_mismatch")
    # Never extract arbitrary paths/symlinks from an archive, even when pinned.
    with zipfile.ZipFile(archive) as zipped:
        for name in ("xray", "geoip.dat", "geosite.dat"):
            info = zipped.getinfo(name)
            if info.is_dir() or info.file_size > MAX_ARCHIVE or stat.S_ISLNK(info.external_attr >> 16):
                raise BootstrapError("bootstrap_xray_archive_unsafe")
            atomic(destination / name, zipped.read(name), 0o755 if name == "xray" else 0o644)


def install_xray(config: dict) -> None:
    run(["install", "-d", "-m", "755", str(XRAY_ASSETS)])
    url = f"https://github.com/XTLS/Xray-core/releases/download/v{config['xray_version']}/Xray-linux-64.zip"
    with tempfile.TemporaryDirectory(prefix="xray-release-", dir=STATE) as temporary:
        work = Path(temporary)
        archive = work / "release.zip"
        started, size = time.monotonic(), 0
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), ReleaseRedirect())
        with opener.open(url, timeout=20) as response, archive.open("xb") as output:
            if response.status != 200:
                raise BootstrapError("bootstrap_release_download_failed")
            while chunk := response.read(256 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE or time.monotonic() - started > 300:
                    raise BootstrapError("bootstrap_release_download_limit")
                output.write(chunk)
        extract_verified_xray(archive, config["xray_sha256"], work)
        atomic(XRAY_BINARY, (work / "xray").read_bytes(), 0o755)
        for name in ("geoip.dat", "geosite.dat"):
            atomic(XRAY_ASSETS / name, (work / name).read_bytes(), 0o644)


def seed() -> dict:
    path = STATE / "seed.json"
    if path.exists():
        return read_object(path, secret=True)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    key = X25519PrivateKey.generate()
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    result = {
        "private_key": encode(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())),
        "public_key": encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
        "short_id": secrets.token_hex(8), "template_uuid": str(uuid.uuid4()),
    }
    atomic(path, json.dumps(result))
    return result


def xray_config(config: dict, keys: dict) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "tag": "emery-template", "listen": "127.0.0.1", "port": 443, "protocol": "vless",
            # This URI is only a template; there is deliberately NO working shared UUID.
            "settings": {"clients": [], "decryption": "none"},
            "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                "show": False, "dest": config["reality_server_name"] + ":443", "xver": 0,
                "serverNames": [config["reality_server_name"]], "privateKey": keys["private_key"],
                "shortIds": [keys["short_id"]],
            }},
            "sniffing": {"enabled": True, "destOverride": ["http", "tls"], "routeOnly": True},
        }],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}, {"tag": "emery-blocked", "protocol": "blackhole"}],
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": [
            {"type": "field", "port": "25,465,587", "outboundTag": "emery-blocked"},
            {"type": "field", "ip": ["geoip:private"], "outboundTag": "emery-blocked"},
        ]},
    }


def service(name: str, content: str) -> None:
    atomic(Path("/etc/systemd/system") / name, content, 0o644)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", name])


def user(name: str) -> None:
    import pwd
    try:
        pwd.getpwnam(name)
    except KeyError:
        run(["useradd", "--system", "--user-group", "--no-create-home", "--shell", "/usr/sbin/nologin", name])


def configure_xray(config: dict) -> None:
    user("emery-xray")
    run(["install", "-d", "-m", "755", str(XRAY_CONFIG.parent)])
    if XRAY_CONFIG.exists() and any(str(row.get("tag", "")).startswith("emery-device-")
                                  for row in read_object(XRAY_CONFIG).get("inbounds", [])):
        raise BootstrapError("bootstrap_refusing_existing_assignments")
    atomic(XRAY_CONFIG, json.dumps(xray_config(config, seed()), indent=2), 0o640)
    import grp
    os.chown(XRAY_CONFIG, 0, grp.getgrnam("emery-xray").gr_gid)
    run([str(XRAY_BINARY), "run", "-test", "-config", str(XRAY_CONFIG)])
    service("emery-ionos-rates.service", """[Unit]
Description=Restore Skryon per-device limits after reboot
After=emery-ionos-firewall.service
Before=xray.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/emery/ionos-bootstrap/ionos-cloud/bootstrap_node.py --restore-rate-limits /opt/emery/ionos-bootstrap/config.json
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
""")
    service("xray.service", """[Unit]
Description=Skryon device-only Xray
After=network-online.target emery-ionos-firewall.service emery-ionos-rates.service
Requires=emery-ionos-firewall.service emery-ionos-rates.service
Wants=network-online.target
[Service]
User=emery-xray
Group=emery-xray
Environment=XRAY_LOCATION_ASSET=/usr/local/share/xray
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
Restart=on-failure
RestartSec=3
LimitNOFILE=65536
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
[Install]
WantedBy=multi-user.target
""")


def firewall_rules(config: dict) -> str:
    return f"""add table inet emery_ionos_ingress
flush table inet emery_ionos_ingress
table inet emery_ionos_ingress {{
 chain input {{ type filter hook input priority -5; policy drop;
  iifname "lo" accept
  ct state established,related accept
  ct state invalid drop
  udp sport 67 udp dport 68 accept
  ip protocol icmp accept
  meta l4proto ipv6-icmp accept
  ip saddr {config['management_ipv4']} tcp dport 22 accept
  tcp dport {{ 80, {config['gate_port']} }} accept
 }}
 chain forward {{ type filter hook forward priority -5; policy drop; }}
 chain output {{ type filter hook output priority -5; policy accept;
  tcp dport {{ 25, 465, 587 }} drop
 }}
}}
"""


def install_firewall(config: dict) -> None:
    # Only our table is replaced, atomically. Other owners' tables are untouched.
    rules = firewall_rules(config)
    run(["nft", "-c", "-f", "-"], data=rules)
    atomic(Path("/etc/emery/ionos-firewall.nft"), rules)
    run(["nft", "-f", "/etc/emery/ionos-firewall.nft"])
    service("emery-ionos-firewall.service", """[Unit]
Description=Skryon IONOS ingress firewall
Before=xray.service emery-device-gate.service
After=network-pre.target
[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f /etc/emery/ionos-firewall.nft
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
""")


def rate_rules(config: dict, source: dict) -> str:
    rows, ports = [], set()
    for inbound in source.get("inbounds", []):
        tag = str(inbound.get("tag", ""))
        if not tag.startswith("emery-device-"):
            if inbound.get("listen") != "127.0.0.1" or inbound.get("settings", {}).get("clients"):
                raise BootstrapError("bootstrap_shared_or_public_listener")
            continue
        match = re.fullmatch(r"emery-device-([1-9][0-9]*)-([1-9][0-9]*)", tag)
        port = inbound.get("port")
        clients = inbound.get("settings", {}).get("clients", [])
        if (not match or inbound.get("protocol") != "vless" or inbound.get("listen") != "127.0.0.1"
                or type(port) is not int or not config["assignment_port_start"] <= port <= config["assignment_port_end"]
                or port in ports or len(clients) != 1 or inbound.get("settings", {}).get("fallbacks")):
            raise BootstrapError("bootstrap_unsafe_device_listener")
        uuid.UUID(clients[0]["id"])
        speed = int(match[2])
        if not 1 <= speed <= 1000:
            raise BootstrapError("bootstrap_invalid_device_rate")
        ports.add(port)
        rows.append((port, speed))
    lines = ["add table inet emery_vpn_rate", "flush table inet emery_vpn_rate", "table inet emery_vpn_rate {"]
    for chain, hook, field in (("ingress", "input", "dport"), ("egress", "output", "sport")):
        lines.append(f" chain {chain} {{ type filter hook {hook} priority filter; policy accept;")
        for port, speed in sorted(rows):
            rate = max(1, speed * 1000 // 8)
            burst = max(1, rate // 5)
            lines.append(f"  tcp {field} {port} limit rate over {rate} kbytes/second burst {burst} kbytes counter drop")
        lines.append(" }")
    return "\n".join(lines + ["}", ""])


def restore_rate_limits(config: dict) -> None:
    rules = rate_rules(config, read_object(XRAY_CONFIG))
    run(["nft", "-c", "-f", "-"], data=rules)
    run(["nft", "-f", "-"], data=rules)


def certificate_paths(config: dict) -> tuple[Path, Path]:
    directory = Path("/etc/letsencrypt/live") / config["hostname"]
    return directory / "fullchain.pem", directory / "privkey.pem"


def certificate_pin(config: dict) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    certificate, _ = certificate_paths(config)
    cert = x509.load_pem_x509_certificate(certificate.read_bytes())
    return hashlib.sha256(cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()


def provision_certificate(config: dict) -> None:
    deadline = time.monotonic() + 240
    while True:
        try:
            addresses = {row[4][0] for row in socket.getaddrinfo(config["hostname"], 80, type=socket.SOCK_STREAM)}
            if addresses == {config["endpoint"]}:
                break
        except socket.gaierror:
            pass
        if time.monotonic() >= deadline:
            raise BootstrapError("bootstrap_public_dns_not_ready")
        time.sleep(5)
    # Explicit acceptance is a validated operator setting, never assumed by code.
    run(["certbot", "certonly", "--standalone", "--non-interactive", "--agree-tos",
         "--email", config["acme_email"], "--cert-name", config["hostname"], "-d", config["hostname"],
         "--server", "https://acme-v02.api.letsencrypt.org/directory", "--reuse-key", "--keep-until-expiring"], timeout=300)
    run(["openssl", "x509", "-in", str(certificate_paths(config)[0]), "-noout", "-checkend", "3600"])


def install_gate(config: dict) -> None:
    user("emery-gate")
    run(["install", "-d", "-m", "755", "/opt/emery/device-gate"])
    cert, key = certificate_paths(config)
    pin = certificate_pin(config)
    env = {
        "EMERY_GATE_BIND_HOST": "0.0.0.0", "EMERY_GATE_BIND_PORT": str(config["gate_port"]),
        "EMERY_GATE_NODE_ID": str(config["node_id"]), "EMERY_GATE_SERVER_NAME": config["hostname"],
        "EMERY_GATE_SPKI_SHA256": pin, "EMERY_GATE_TLS_CERT_FILE": str(cert), "EMERY_GATE_TLS_KEY_FILE": str(key),
        "EMERY_GATE_AUTHORIZE_URL": config["authorize_url"], "EMERY_GATE_AUTHORIZE_KEY": config["authorize_key"],
        "EMERY_GATE_REGIONAL_POLICY_STATE_FILE": str(REGIONAL_STATE / "ready.json"),
    }
    # systemd reads this root-only file before dropping to the gate user.
    atomic(Path("/etc/emery/device-gate.env"), "".join(f"{name}={value}\n" for name, value in env.items()))
    atomic(Path("/opt/emery/device-gate/emery_device_gate.py"), (ROOT / "device-gate/emery_device_gate.py").read_bytes(), 0o644)
    for path in (Path("/etc/letsencrypt"), Path("/etc/letsencrypt/live"), cert.parent,
                 Path("/etc/letsencrypt/archive"), key.resolve().parent):
        run(["setfacl", "-m", "u:emery-gate:--x", str(path)])
    for path in (cert.resolve(), key.resolve()):
        run(["setfacl", "-m", "u:emery-gate:r--", str(path)])
    # Renewal keeps the TLS private key (and pin). Unexpected key rotation must
    # fail closed, not silently replace a pin trusted by already enrolled devices.
    hook = f"""#!/bin/sh
set -eu
[ "$RENEWED_LINEAGE" = "/etc/letsencrypt/live/{config['hostname']}" ] || exit 0
pin=$(openssl x509 -in "$RENEWED_LINEAGE/fullchain.pem" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum)
[ "${{pin%% *}}" = "{pin}" ] || {{ systemctl stop emery-device-gate; exit 1; }}
setfacl -m u:emery-gate:r-- "$(readlink -f "$RENEWED_LINEAGE/fullchain.pem")" "$(readlink -f "$RENEWED_LINEAGE/privkey.pem")"
systemctl restart emery-device-gate
"""
    atomic(Path("/etc/letsencrypt/renewal-hooks/deploy/emery-device-gate"), hook, 0o700)
    # Wants (not Requires) preserves the gate process during per-device Xray
    # restarts; no connection can pass until its own loopback listener is ready.
    service("emery-device-gate.service", """[Unit]
Description=Skryon signed-device TLS gateway
After=network-online.target xray.service emery-ionos-firewall.service
Wants=network-online.target xray.service
[Service]
User=emery-gate
Group=emery-gate
EnvironmentFile=/etc/emery/device-gate.env
ExecStart=/usr/bin/python3 /opt/emery/device-gate/emery_device_gate.py
Restart=on-failure
RestartSec=2
UMask=0077
LimitNOFILE=65536
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
AmbientCapabilities=
[Install]
WantedBy=multi-user.target
""")
    run(["systemctl", "enable", "--now", "certbot.timer"])


def regional_ready() -> None:
    value = read_object(REGIONAL_STATE / "ready.json")
    if (value.get("schema") != 1 or value.get("policy") != "russia" or value.get("listen_host") != "127.0.0.2"
            or not 0 <= time.time() - float(value.get("updated_at", 0)) < 48 * 3600):
        raise BootstrapError("bootstrap_regional_policy_not_ready")
    run(["systemctl", "is-active", "--quiet", "emery-regional-xray.service"])


def install_regional_policy() -> None:
    run(["bash", str(ROOT / "regional-policy/install.sh")])
    # Large lists are downloaded here on the server, before publication, and by
    # its timer later. Neither Android nor a connection request runs this step.
    run(["python3", "/opt/emery/regional-policy/regional_policy.py", "update"], timeout=2400)
    run(["systemctl", "enable", "--now", "emery-regional-policy-update.timer"])
    regional_ready()


def control_probe(config: dict) -> None:
    now = str(int(time.time() * 1000))
    body = {
        "protocol_version": 2, "regional_policy": "russia", "operation": "check",
        "assignment_id": CANARY_ID, "node_id": config["node_id"], "gate_server_name": config["hostname"],
        "gate_spki_sha256": certificate_pin(config), "device_id": "bootstrap-" + uuid.uuid4().hex,
        "server_issued_at": now, "timestamp": now,
        "server_nonce": secrets.token_urlsafe(32), "client_nonce": secrets.token_urlsafe(32),
        "signature": base64.b64encode(secrets.token_bytes(64)).decode(), "signature_algorithm": "SHA256withECDSA",
    }
    request = urllib.request.Request(config["authorize_url"], data=json.dumps(body).encode(),
                                    headers={"Content-Type": "application/json", "X-Device-Gate-Key": config["authorize_key"]})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=15):
            raise BootstrapError("bootstrap_control_accepted_unsigned_device")
    except urllib.error.HTTPError as exc:
        with exc:
            raw = exc.read(8193)
            if exc.code != 403 or len(raw) > 8192:
                raise BootstrapError("bootstrap_control_not_ready")
            result = json.loads(raw)
        # Correct service key + unknown device must give this precise denial.
        # A wrong gate key returns device_gate_forbidden, not this reason.
        if result.get("ok") is not False or result.get("reason") != "device_gate_not_authorized":
            raise BootstrapError("bootstrap_control_not_ready")


def template_uri(config: dict, keys: dict) -> str:
    query = urllib.parse.urlencode({"encryption": "none", "security": "reality", "sni": config["reality_server_name"],
                                   "fp": "chrome", "pbk": keys["public_key"], "sid": keys["short_id"],
                                   "type": "tcp", "flow": "xtls-rprx-vision"})
    return f"vless://{keys['template_uuid']}@{config['endpoint']}:443?{query}#Skryon"


def bootstrap(config: dict) -> None:
    marker_path = STATE / "operation.json"
    public_config = {key: value for key, value in config.items() if key != "authorize_key"}
    fingerprint = hashlib.sha256(json.dumps(public_config, sort_keys=True).encode()).hexdigest()
    if marker_path.exists():
        marker = read_object(marker_path, secret=True)
        if marker.get("operation_id") != config["operation_id"] or marker.get("fingerprint") != fingerprint:
            raise BootstrapError("bootstrap_existing_operation_conflict")
    else:
        if XRAY_CONFIG.exists() or (STATE / "ready.json").exists():
            raise BootstrapError("bootstrap_refusing_existing_node")
        marker = {"operation_id": config["operation_id"], "fingerprint": fingerprint, "completed": []}
        atomic(marker_path, json.dumps(marker))
    stages = (
        ("packages", install_packages),
        ("xray_binary", lambda: install_xray(config)),
        ("firewall", lambda: install_firewall(config)),
        ("xray_config", lambda: configure_xray(config)),
        ("certificate", lambda: provision_certificate(config)),
        ("gate", lambda: install_gate(config)),
        ("regional", install_regional_policy),
    )
    for name, action in stages:
        if name not in marker["completed"]:
            action()
            marker["completed"].append(name)
            atomic(marker_path, json.dumps(marker))
    run(["systemctl", "is-active", "--quiet", "xray.service", "emery-device-gate.service", "emery-ionos-firewall.service"])
    run([str(XRAY_BINARY), "run", "-test", "-config", str(XRAY_CONFIG)])
    regional_ready()
    control_probe(config)
    atomic(STATE / "ready.json", json.dumps({
        "operation_id": config["operation_id"], "hostname": config["hostname"], "endpoint": config["endpoint"],
        "spki_sha256": certificate_pin(config), "config_payload": template_uri(config, seed()),
        "bootstrap_verified": True, "regional_policy_ready": True,
        "control_api_verified": True, "certificate_verified": True,
    }))


def smoke(config: dict) -> None:
    """Exercise an installed, temporary credential in BOTH isolated Xray planes."""
    payload = json.loads(sys.stdin.read(4097))
    credential = str(uuid.UUID(payload["uuid"]))
    port = payload["port"]
    if type(port) is not int or port != config["assignment_port_start"]:
        raise BootstrapError("bootstrap_invalid_canary_port")
    regional_ready()
    ready = read_object(REGIONAL_STATE / "ready.json")
    if ready.get("assignments", {}).get(str(port)) != CANARY_ID:
        raise BootstrapError("bootstrap_canary_missing_regional_assignment")
    keys = seed()
    for host in ("127.0.0.1", "127.0.0.2"):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            socks_port = reservation.getsockname()[1]
        client_config = {
            "log": {"loglevel": "none"},
            "inbounds": [{"listen": "127.0.0.1", "port": socks_port, "protocol": "socks", "settings": {"auth": "noauth", "udp": False}}],
            "outbounds": [{"protocol": "vless", "settings": {"vnext": [{"address": host, "port": port, "users": [
                {"id": credential, "encryption": "none", "flow": "xtls-rprx-vision"},
            ]}]}, "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                "serverName": config["reality_server_name"], "fingerprint": "chrome", "publicKey": keys["public_key"], "shortId": keys["short_id"],
            }}}],
        }
        with tempfile.TemporaryDirectory(prefix="canary-", dir=STATE) as temporary:
            path = Path(temporary) / "client.json"
            atomic(path, json.dumps(client_config))
            process = subprocess.Popen([str(XRAY_BINARY), "run", "-config", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 3
                while True:
                    if process.poll() is not None:
                        raise BootstrapError("bootstrap_canary_client_failed")
                    try:
                        with socket.create_connection(("127.0.0.1", socks_port), timeout=0.2):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise BootstrapError("bootstrap_canary_client_not_ready")
                        time.sleep(0.1)
                run(["curl", "--silent", "--show-error", "--fail", "--max-time", "10", "--noproxy", "",
                     "--socks5-hostname", f"127.0.0.1:{socks_port}", "--output", "/dev/null", config["probe_url"]], timeout=12)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
    print(json.dumps({"ok": True}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--smoke", action="store_true")
    modes.add_argument("--restore-rate-limits", action="store_true")
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or args.config != ROOT / "config.json":
        raise BootstrapError("bootstrap_root_and_fixed_config_required")
    os.umask(0o077)
    config = read_object(args.config, secret=True)
    validate(config)
    if args.restore_rate_limits:
        # The credential installer may hold its own lock while restarting Xray.
        # Only read its atomically replaced config; never take that lock here.
        restore_rate_limits(config)
        return
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise BootstrapError("bootstrap_unsafe_state_directory")
    with (STATE / "bootstrap.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.smoke:
            smoke(config)
        else:
            try:
                bootstrap(config)
            except Exception as exc:
                # No captured command output, URLs, config or credentials enter
                # systemd's journal or controller responses.
                atomic(STATE / "error.json", json.dumps({"operation_id": config["operation_id"], "error": safe_error(exc)}))
                raise


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("IONOS bootstrap failed: " + safe_error(error), file=sys.stderr)
        raise SystemExit(1)
