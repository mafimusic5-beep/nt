"""Local operator input only. No account, billing, DNS, or ordering API."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from src.backend.services.ionos_cloud_config import (
    IonosConfigurationError, _https_url, dns_name, public_ipv4,
)
from src.common.config import settings


class ManualVpsError(ValueError):
    """Constant, non-secret error codes only."""


def private_file(path: str, *, limit: int = 65536) -> str:
    """Reject symlinks, shared files, huge input and other users' credentials."""
    if not path or not Path(path).is_absolute():
        raise ManualVpsError("manual_vps_absolute_private_file_required")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or info.st_mode & 0o077 or info.st_size > limit):
            raise ManualVpsError("manual_vps_unsafe_private_file")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = None
            value = stream.read(limit + 1)
        if len(value.encode()) > limit:
            raise ManualVpsError("manual_vps_input_too_large")
        return value
    except (OSError, UnicodeError) as exc:
        raise ManualVpsError("manual_vps_private_file_unreadable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def private_object(path: str) -> dict:
    try:
        value = json.loads(private_file(path))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ManualVpsError):
            raise
        raise ManualVpsError("manual_vps_invalid_json_object") from exc


def setup_guard() -> None:
    if not settings.manual_vps_setup_enabled:
        raise ManualVpsError("manual_vps_setup_disabled")
    if settings.auto_provision_enabled or settings.ionos_cloud_apply_enabled:
        raise ManualVpsError("manual_vps_disable_automatic_purchases_first")
    for flag in (
        "pool_accounting_bridge_enabled", "unique_device_credentials_enabled",
        "per_device_rate_limit_enforced", "smtp_abuse_protection_enabled", "device_bound_gate_enabled",
    ):
        if not getattr(settings, flag):
            raise ManualVpsError("manual_vps_required_" + flag)
    if not settings.pool_bridge_api_key.strip():
        raise ManualVpsError("manual_vps_pool_bridge_key_required")
    if settings.recovery_ssh_user != "root" or settings.recovery_allow_unknown_host_keys:
        raise ManualVpsError("manual_vps_pinned_root_ssh_required")
    if (settings.xray_config_path != "/usr/local/etc/xray/config.json"
            or settings.xray_credential_script.strip()
            or settings.device_gate_service_name != "emery-device-gate"
            or settings.regional_policy_sync_script != "/opt/emery/regional-policy/regional_policy.py"):
        raise ManualVpsError("manual_vps_incompatible_node_transport")


def bootstrap_profile() -> dict:
    """Secret-free installation snapshot; deliberate local config, never APK data."""
    setup_guard()
    value = private_object(settings.manual_vps_profile_path)
    required = {
        "management_ipv4", "authorize_url", "acme_email", "acme_terms_accepted",
        "xray_version", "xray_sha256", "reality_server_name", "probe_url",
        "bootstrap_timeout_seconds",
    }
    if set(value) != required:
        raise ManualVpsError("manual_vps_profile_fields_invalid")
    try:
        value["management_ipv4"] = public_ipv4(value["management_ipv4"])
        value["authorize_url"] = _https_url(value["authorize_url"], path="/internal/device-gate/authorize")
        value["probe_url"] = _https_url(value["probe_url"])
        value["reality_server_name"] = dns_name(value["reality_server_name"])
        if (value["acme_terms_accepted"] is not True
                or not isinstance(value["acme_email"], str)
                or len(value["acme_email"]) > 254
                or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value["acme_email"])):
            raise ManualVpsError("manual_vps_acme_consent_and_email_required")
        if (not isinstance(value["xray_version"], str)
                or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value["xray_version"])
                or not isinstance(value["xray_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", value["xray_sha256"])):
            raise ManualVpsError("manual_vps_pinned_xray_release_required")
        timeout = value["bootstrap_timeout_seconds"]
        if type(timeout) is not int or not 3600 <= timeout <= 14400:
            raise ManualVpsError("manual_vps_invalid_bootstrap_deadline")
        start, end = settings.xray_client_port_start, settings.xray_client_port_end
        if not (1024 <= start <= end <= 65535) or start <= 24443 <= end:
            raise ManualVpsError("manual_vps_invalid_assignment_ports")
        key = settings.manual_vps_gate_authorize_key.get_secret_value()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", key):
            raise ManualVpsError("manual_vps_gate_authorize_key_required")
        value.update(
            assignment_port_start=start, assignment_port_end=end, gate_port=24443,
            authorize_key_sha256=hashlib.sha256(key.encode()).hexdigest(),
        )
        return value
    except (IonosConfigurationError, TypeError, AttributeError) as exc:
        raise ManualVpsError("manual_vps_profile_invalid") from exc


@dataclass(frozen=True)
class ManualVpsSpec:
    endpoint: str
    hostname: str
    region_code: str
    name: str
    capacity_clients: int
    bandwidth_limit_mbps: int
    ssh_private_key: str = field(repr=False)
    ssh_public_key: str
    ssh_host_key: str
    ssh_key_fingerprint: str

    def snapshot(self) -> dict:
        return {
            "endpoint": self.endpoint, "hostname": self.hostname, "region_code": self.region_code,
            "name": self.name, "capacity_clients": self.capacity_clients,
            "bandwidth_limit_mbps": self.bandwidth_limit_mbps,
            "per_device_speed_limit_mbps": settings.pool_per_device_speed_limit_mbps,
            "ssh_public_key": self.ssh_public_key, "ssh_host_key": self.ssh_host_key,
            "ssh_key_sha256": hashlib.sha256(self.ssh_private_key.encode()).hexdigest(),
        }


def node_spec(path: str) -> ManualVpsSpec:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

    value = private_object(path)
    expected = {"endpoint", "hostname", "region_code", "name", "capacity_clients",
                "bandwidth_limit_mbps", "ssh_private_key_path", "ssh_host_key"}
    if set(value) != expected:
        raise ManualVpsError("manual_vps_node_fields_invalid")
    try:
        endpoint, hostname = public_ipv4(value["endpoint"]), dns_name(value["hostname"])
        region, name = value["region_code"], value["name"]
        if not isinstance(region, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,15}", region):
            raise ValueError()
        if not isinstance(name, str) or not 1 <= len(name) <= 128 or not name.isprintable():
            raise ValueError()
        capacity, bandwidth = value["capacity_clients"], value["bandwidth_limit_mbps"]
        ports = settings.xray_client_port_end - settings.xray_client_port_start + 1
        if type(capacity) is not int or not 1 <= capacity <= min(10000, ports):
            raise ValueError()
        if (type(bandwidth) is not int or not 1 <= bandwidth <= 100000
                or not 1 <= settings.pool_per_device_speed_limit_mbps <= bandwidth):
            raise ValueError()
        # Require the existing, verified Ed25519 host public key, not a scan to
        # be silently trusted on first use. It is obtained via provider console.
        host_line = value["ssh_host_key"].strip()
        parts = host_line.split()
        if len(parts) < 2 or len(host_line.splitlines()) != 1 or parts[0] != "ssh-ed25519":
            raise ValueError()
        parts = parts[:2]  # An ordinary trailing OpenSSH comment is not identity.
        host = serialization.load_ssh_public_key(" ".join(parts).encode())
        if not isinstance(host, Ed25519PublicKey):
            raise ValueError()
        private = private_file(value["ssh_private_key_path"], limit=16384).strip()
        key = serialization.load_ssh_private_key(private.encode(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError()
        public = key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
        ).decode()
        fingerprint = "SHA256:" + base64.b64encode(
            hashlib.sha256(base64.b64decode(public.split()[1])).digest(),
        ).decode().rstrip("=")
        return ManualVpsSpec(endpoint, hostname, region, name, capacity, bandwidth,
                             private, public, " ".join(parts), fingerprint)
    except (ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, ManualVpsError):
            raise
        raise ManualVpsError("manual_vps_node_spec_invalid") from exc
