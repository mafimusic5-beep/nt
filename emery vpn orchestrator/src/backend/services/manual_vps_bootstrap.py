"""Reuse the installed-node pipeline without creating a provider API client."""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import socket
import ssl
from urllib.parse import urlsplit

from src.backend.services.ionos_cloud_bootstrap import DEPLOY_ROOT, IonosSshBootstrap
from src.backend.services.manual_vps_config import (
    CONTROLLER_ONLY_PROFILE_FIELDS,
    ManualVpsError,
    public_metadata_is_forbidden,
    require_neutral_public_name,
)
from src.common.config import settings

PREFLIGHT_HELPER = DEPLOY_ROOT / "manual-vps/preflight_node.py"


def helper_digest() -> str:
    return hashlib.sha256(PREFLIGHT_HELPER.read_bytes()).hexdigest()


def resolve_host(hostname: str) -> set[str]:
    try:
        return {row[4][0] for row in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ManualVpsError("manual_vps_dns_not_ready") from exc


def reverse_names(address: str) -> set[str]:
    try:
        primary, aliases, _ = socket.gethostbyaddr(address)
        return {str(value).lower().rstrip(".") for value in (primary, *aliases) if value}
    except (OSError, socket.herror, socket.gaierror):
        # Not having PTR is acceptable. A PTR that exists is inspected below.
        return set()


def tcp_port_open(address: str, port: int) -> bool:
    try:
        with socket.create_connection((address, port), timeout=2):
            return True
    except OSError:
        return False


def generic_tls_is_rejected(address: str, port: int) -> bool:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((address, port), timeout=3) as raw:
            with context.wrap_socket(raw):
                return False
    except (OSError, ssl.SSLError):
        return True


def public_gateway_metadata(address: str, port: int, hostname: str) -> dict:
    """Read only bounded public data; never return raw traffic or cert bytes."""
    try:
        with socket.create_connection((address, port), timeout=5) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=hostname) as secured:
                secured.settimeout(5)
                certificate = secured.getpeercert()
                dns_names = {
                    value.lower().rstrip(".")
                    for kind, value in certificate.get("subjectAltName", ())
                    if kind == "DNS"
                }
                subject_values = {
                    str(value).casefold()
                    for row in certificate.get("subject", ())
                    for _, value in row
                }
                with secured.makefile("rb") as stream:
                    challenge_line = stream.readline(8193)
                    if not challenge_line or len(challenge_line) > 8192:
                        raise ManualVpsError("manual_vps_public_gateway_metadata_invalid")
                    challenge = json.loads(challenge_line)
                    secured.sendall(b"{}\n")
                    denial_line = stream.readline(8193)
                    if not denial_line or len(denial_line) > 8192:
                        raise ManualVpsError("manual_vps_public_gateway_metadata_invalid")
                    denial = json.loads(denial_line)
        return {
            "dns_names": dns_names,
            "subject_values": subject_values,
            "challenge": challenge,
            "denial": denial,
        }
    except ManualVpsError:
        raise
    except (OSError, ssl.SSLError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ManualVpsError("manual_vps_public_gateway_metadata_unavailable") from exc


class ManualVpsBootstrap(IonosSshBootstrap):
    def __init__(self):
        super().__init__(authorize_key=settings.manual_vps_gate_authorize_key.get_secret_value())

    @staticmethod
    def check_dns(node, profile: dict) -> None:
        # No API is used to change DNS. An existing DNS-only A record must
        # resolve exclusively to this VPS; a stale AAAA/proxy record is unsafe.
        if resolve_host(profile["hostname"]) != {node.endpoint}:
            raise ManualVpsError("manual_vps_dns_must_point_only_to_selected_ipv4")
        require_neutral_public_name(profile["hostname"], profile)
        authorize_host = urlsplit(profile["authorize_url"]).hostname
        for hostname in profile["separate_service_hosts"]:
            if node.endpoint in resolve_host(hostname):
                if hostname == authorize_host:
                    raise ManualVpsError("manual_vps_refusing_authorization_server")
                raise ManualVpsError("manual_vps_refusing_separate_service_host")

    @staticmethod
    def server_profile(profile: dict) -> dict:
        """Never copy brand/site/bot affiliation metadata onto the VPN host."""
        return {key: value for key, value in profile.items() if key not in CONTROLLER_ONLY_PROFILE_FIELDS}

    def preflight(self, node, job, profile: dict, *, claim: bool = False) -> None:
        if helper_digest() != profile["preflight_sha256"]:
            raise ManualVpsError("manual_vps_preflight_helper_changed")
        self.check_dns(node, profile)
        payload = {
            "action": "claim" if claim else "inspect", "operation_id": job.id, "node_id": node.id,
            "management_ipv4": profile["management_ipv4"],
            "profile_sha256": hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest(),
        }
        client = self.ssh._connect(node)
        try:
            result = json.loads(self._exec(
                client, "/usr/bin/python3 -c " + shlex.quote(PREFLIGHT_HELPER.read_text()),
                data=json.dumps(payload), timeout=45,
            ))
            if not isinstance(result, dict) or result.get("ok") is not True:
                detail = str(result.get("detail", "")) if isinstance(result, dict) else ""
                if not re.fullmatch(r"manual_vps_[a-z0-9_]{1,100}", detail):
                    detail = "manual_vps_remote_preflight_failed"
                raise ManualVpsError(detail)
        finally:
            client.close()

    def start(self, node, job, profile: dict) -> None:
        # Repeat the safety check immediately before mutation. A durable remote
        # ownership marker makes retries safe even if an SSH response was lost.
        self.preflight(node, job, profile, claim=True)
        super().start(node, job, self.server_profile(profile))

    @staticmethod
    def verify_public_metadata(node, profile: dict) -> None:
        if profile.get("public_metadata_hardening") is not True:
            raise ManualVpsError("manual_vps_public_metadata_hardening_required")
        hostname = require_neutral_public_name(profile["hostname"], profile)
        for name in reverse_names(node.endpoint):
            if public_metadata_is_forbidden(name, profile, dns=True):
                raise ManualVpsError("manual_vps_public_ptr_exposes_identity")
        metadata = public_gateway_metadata(node.endpoint, profile["gate_port"], hostname)
        if metadata["dns_names"] != {hostname}:
            raise ManualVpsError("manual_vps_public_certificate_names_invalid")
        if any(public_metadata_is_forbidden(value, profile) for value in metadata["subject_values"]):
            raise ManualVpsError("manual_vps_public_certificate_exposes_identity")
        challenge = metadata["challenge"]
        if (not isinstance(challenge, dict)
                or set(challenge) != {"version", "server_issued_at", "server_nonce"}
                or challenge.get("version") != 1
                or not isinstance(challenge.get("server_issued_at"), str)
                or not challenge["server_issued_at"].isdigit()
                or not isinstance(challenge.get("server_nonce"), str)
                or not 32 <= len(challenge["server_nonce"]) <= 128
                or metadata["denial"] != {"ok": False}):
            raise ManualVpsError("manual_vps_public_gateway_metadata_invalid")
        if not generic_tls_is_rejected(node.endpoint, profile["gate_port"]):
            raise ManualVpsError("manual_vps_gateway_accepts_unknown_sni")
        if any(tcp_port_open(node.endpoint, port) for port in (80, 443)):
            raise ManualVpsError("manual_vps_unexpected_public_service")

    def verify_data_plane(self, node, job, profile: dict) -> None:
        self.check_dns(node, profile)
        super().verify_data_plane(node, job, profile)
        self.verify_public_metadata(node, profile)
