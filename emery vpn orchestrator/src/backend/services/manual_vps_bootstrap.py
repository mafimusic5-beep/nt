"""Reuse the installed-node pipeline without creating a provider API client."""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import socket
from urllib.parse import urlsplit

from src.backend.services.ionos_cloud_bootstrap import DEPLOY_ROOT, IonosSshBootstrap
from src.backend.services.manual_vps_config import ManualVpsError
from src.common.config import settings

PREFLIGHT_HELPER = DEPLOY_ROOT / "manual-vps/preflight_node.py"


def helper_digest() -> str:
    return hashlib.sha256(PREFLIGHT_HELPER.read_bytes()).hexdigest()


def resolve_host(hostname: str) -> set[str]:
    try:
        return {row[4][0] for row in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ManualVpsError("manual_vps_dns_not_ready") from exc


class ManualVpsBootstrap(IonosSshBootstrap):
    def __init__(self):
        super().__init__(authorize_key=settings.manual_vps_gate_authorize_key.get_secret_value())

    @staticmethod
    def check_dns(node, profile: dict) -> None:
        # No API is used to change DNS. An existing DNS-only A record must
        # resolve exclusively to this VPS; a stale AAAA/proxy record is unsafe.
        if resolve_host(profile["hostname"]) != {node.endpoint}:
            raise ManualVpsError("manual_vps_dns_must_point_only_to_selected_ipv4")
        if node.endpoint in resolve_host(urlsplit(profile["authorize_url"]).hostname):
            raise ManualVpsError("manual_vps_refusing_authorization_server")

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
        super().start(node, job, profile)

    def verify_data_plane(self, node, job, profile: dict) -> None:
        self.check_dns(node, profile)
        super().verify_data_plane(node, job, profile)
