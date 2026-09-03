"""Validated, server-owned IONOS ordering profiles; no fields come from the APK."""
from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlsplit
from uuid import UUID

from src.common.config import settings


class IonosConfigurationError(ValueError):
    pass


def valid_uuid(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise IonosConfigurationError("ionos_invalid_resource_id") from exc


def public_ipv4(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
        if address.version != 4 or not address.is_global:
            raise ValueError()
        return str(address)
    except ValueError as exc:
        raise IonosConfigurationError("ionos_public_ipv4_required") from exc


def dns_name(value: str) -> str:
    name = str(value).strip().lower().rstrip(".")
    if len(name) > 200 or "." not in name or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in name.split(".")
    ):
        raise IonosConfigurationError("ionos_invalid_dns_name")
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name
    raise IonosConfigurationError("ionos_dns_name_not_ip_required")


def _https_url(value: str, *, path: str | None = None) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment or parsed.query
            or parsed.port not in (None, 443) or (path is not None and parsed.path != path)
        ):
            raise ValueError()
        dns_name(parsed.hostname)
        if any(ord(c) <= 32 for c in value):
            raise ValueError()
    except ValueError as exc:
        raise IonosConfigurationError("ionos_valid_https_url_required") from exc
    return value


def ordering_profile(region_code: str) -> dict:
    """Validate locally before any paid request. Defaults deliberately cannot order."""
    try:
        profiles = json.loads(settings.ionos_cloud_region_profiles_json)
        profile = profiles.get(region_code) if isinstance(profiles, dict) else None
        if not isinstance(profile, dict):
            raise ValueError()
        if set(profile) - {"location", "image_id", "cores", "ram_mb", "disk_gb", "server_type"}:
            raise ValueError()
        location = str(profile["location"])
        if not re.fullmatch(r"[a-z]{2}/[a-z0-9-]{2,16}", location):
            raise ValueError()
        size = {key: profile[key] for key in ("cores", "ram_mb", "disk_gb")}
        if any(type(value) is not int for value in size.values()):
            raise ValueError()
        if not (1 <= size["cores"] <= 32 and 2048 <= size["ram_mb"] <= 131072
                and size["ram_mb"] % 256 == 0 and 10 <= size["disk_gb"] <= 1000):
            raise ValueError()
        server_type = profile.get("server_type", "VCPU")
        if server_type not in {"VCPU", "ENTERPRISE"}:
            raise ValueError()
        result = dict(size, location=location, image_id=valid_uuid(profile["image_id"]), server_type=server_type)
    except (ValueError, TypeError, KeyError) as exc:
        raise IonosConfigurationError("ionos_region_profile_invalid") from exc

    if not settings.ionos_cloud_token.get_secret_value().strip():
        raise IonosConfigurationError("ionos_token_not_configured")
    contract = settings.ionos_cloud_contract_number.strip()
    if not re.fullmatch(r"[1-9][0-9]{0,19}", contract):
        raise IonosConfigurationError("ionos_contract_number_required")
    if not settings.device_bound_gate_enabled:
        raise IonosConfigurationError("ionos_device_gate_required")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", settings.ionos_cloud_gate_authorize_key.get_secret_value()):
        raise IonosConfigurationError("ionos_gate_authorize_key_required")
    if not settings.ionos_cloud_acme_terms_accepted:
        raise IonosConfigurationError("ionos_acme_terms_not_accepted")
    email = settings.ionos_cloud_acme_email.strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
        raise IonosConfigurationError("ionos_acme_email_required")
    version = settings.ionos_cloud_xray_version.strip().removeprefix("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise IonosConfigurationError("ionos_pinned_xray_version_required")
    checksum = settings.ionos_cloud_xray_sha256.strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise IonosConfigurationError("ionos_pinned_xray_checksum_required")
    if settings.recovery_ssh_user != "root":
        raise IonosConfigurationError("ionos_root_key_ssh_required")
    if settings.xray_config_path != "/usr/local/etc/xray/config.json":
        raise IonosConfigurationError("ionos_xray_config_path_mismatch")
    if settings.xray_credential_script.strip():
        raise IonosConfigurationError("ionos_builtin_credential_transport_required")
    if settings.device_gate_service_name != "emery-device-gate":
        raise IonosConfigurationError("ionos_gate_service_name_mismatch")
    if settings.regional_policy_sync_script != "/opt/emery/regional-policy/regional_policy.py":
        raise IonosConfigurationError("ionos_regional_helper_path_mismatch")
    if not 3600 <= settings.ionos_cloud_bootstrap_timeout_seconds <= 14400:
        raise IonosConfigurationError("ionos_invalid_bootstrap_deadline")
    suffix = dns_name(settings.ionos_cloud_domain_suffix)
    if len(suffix) > 150:
        raise IonosConfigurationError("ionos_domain_suffix_too_long")
    if not (1024 <= settings.xray_client_port_start <= settings.xray_client_port_end <= 65535):
        raise IonosConfigurationError("ionos_invalid_assignment_port_range")
    if settings.xray_client_port_start <= 24443 <= settings.xray_client_port_end:
        raise IonosConfigurationError("ionos_gate_port_conflicts_with_assignment_range")
    result.update(
        contract_number=contract,
        management_ipv4=public_ipv4(settings.ionos_cloud_management_ipv4.strip()),
        dns_zone_id=valid_uuid(settings.ionos_cloud_dns_zone_id),
        domain_suffix=suffix,
        authorize_url=_https_url(settings.ionos_cloud_gate_authorize_url, path="/internal/device-gate/authorize"),
        acme_email=email, acme_terms_accepted=True, xray_version=version, xray_sha256=checksum,
        reality_server_name=dns_name(settings.ionos_cloud_reality_server_name),
        probe_url=_https_url(settings.ionos_cloud_probe_url),
        assignment_port_start=settings.xray_client_port_start,
        assignment_port_end=settings.xray_client_port_end,
        gate_port=24443,
        bootstrap_timeout_seconds=settings.ionos_cloud_bootstrap_timeout_seconds,
    )
    return result
