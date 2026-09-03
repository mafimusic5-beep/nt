"""Small allowlisted IONOS Cloud v6 + Cloud DNS client (no retail checkout API)."""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from src.backend.services.ionos_cloud_config import valid_uuid
from src.common.config import settings

CLOUD_BASE = "https://api.ionos.com/cloudapi/v6"
DNS_BASE = "https://dns.de-fra.ionos.com"


@dataclass
class IonosApiError(Exception):
    detail: str
    status_code: int = 0
    uncertain: bool = False

    def __str__(self) -> str:
        # Never include provider response bodies, request payloads or tokens.
        return self.detail


class IonosCloudApi:
    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def request(self, method: str, path: str, *, payload: dict | None = None,
                params: dict | None = None, dns: bool = False, allow_missing: bool = False) -> dict | None:
        if method not in {"GET", "POST", "PUT"} or not re.fullmatch(r"/[A-Za-z0-9_./-]+", path) or ".." in path:
            raise IonosApiError("ionos_request_not_allowed")
        token = settings.ionos_cloud_token.get_secret_value().strip()
        if dns:
            token = settings.ionos_cloud_dns_token.get_secret_value().strip() or token
        if not token:
            raise IonosApiError("ionos_token_not_configured")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if not dns:
            headers["X-Contract-Number"] = settings.ionos_cloud_contract_number
        try:
            # Explicitly disable redirects/proxy inheritance. In particular a
            # provider response can never redirect the bearer token elsewhere.
            with httpx.Client(transport=self.transport, timeout=20, follow_redirects=False, trust_env=False) as client:
                with client.stream(method, (DNS_BASE if dns else CLOUD_BASE) + path,
                                   headers=headers, json=payload, params=params) as response:
                    if response.status_code == 404 and allow_missing:
                        return None
                    if response.status_code not in {200, 201, 202}:
                        raise IonosApiError(f"ionos_http_{response.status_code}", response.status_code,
                                            method != "GET" and response.status_code >= 500)
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 4 * 1024 * 1024:
                            raise IonosApiError("ionos_response_too_large", uncertain=method != "GET")
                    import json
                    try:
                        value = json.loads(content)
                    except (ValueError, UnicodeError) as exc:
                        raise IonosApiError("ionos_invalid_json", uncertain=method != "GET") from exc
                    if not isinstance(value, dict):
                        raise IonosApiError("ionos_invalid_object", uncertain=method != "GET")
                    return value
        except httpx.HTTPError as exc:
            raise IonosApiError("ionos_transport_error", uncertain=method != "GET") from exc

    def items(self, path: str, *, dns: bool = False) -> list[dict]:
        result: list[dict] = []
        # No arbitrary provider-supplied pagination URLs are followed.
        for offset in range(0, 10000, 100):
            page = self.request("GET", path, params={"depth": 1, "limit": 100, "offset": offset} if not dns
                                else {"limit": 100, "offset": offset}, dns=dns)
            items = page.get("items")
            if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
                raise IonosApiError("ionos_invalid_collection")
            result.extend(items)
            if len(items) < 100:
                return result
        raise IonosApiError("ionos_collection_limit_exceeded")

    def preflight(self, profile: dict) -> None:
        image = self.request("GET", f"/images/{valid_uuid(profile['image_id'])}")
        props = image.get("properties", {})
        if props.get("licenceType") != "LINUX" or props.get("public") is not True or props.get("cloudInit") != "V1":
            raise IonosApiError("ionos_public_cloud_init_linux_image_required")
        if props.get("location") != profile["location"] or float(props.get("size") or 0) > profile["disk_gb"]:
            raise IonosApiError("ionos_image_region_or_size_mismatch")
        # The bootstrap uses Debian's package manager and systemd.
        if "debian" not in str(props.get("name", "")).lower():
            raise IonosApiError("ionos_debian_image_required")
        zone = self.request("GET", f"/zones/{valid_uuid(profile['dns_zone_id'])}", dns=True)
        zone_name = str(zone.get("properties", {}).get("name", "")).lower().rstrip(".")
        suffix = profile["domain_suffix"]
        if zone.get("properties", {}).get("enabled") is not True:
            raise IonosApiError("ionos_dns_zone_disabled")
        if not zone_name or not (suffix == zone_name or suffix.endswith("." + zone_name)):
            raise IonosApiError("ionos_dns_zone_mismatch")

    def ensure_dns_record(self, *, zone_id: str, record_id: str, hostname: str, address: str) -> None:
        zone = self.request("GET", f"/zones/{valid_uuid(zone_id)}", dns=True)
        zone_name = str(zone.get("properties", {}).get("name", "")).lower().rstrip(".")
        if not zone_name or not hostname.endswith("." + zone_name) or zone.get("properties", {}).get("enabled") is not True:
            raise IonosApiError("ionos_dns_zone_mismatch")
        # Cloud DNS record.name is relative to its zone; metadata.fqdn is the
        # absolute result. Sending the FQDN as name can duplicate the suffix.
        relative_name = hostname[:-(len(zone_name) + 1)]
        path = f"/zones/{valid_uuid(zone_id)}/records/{valid_uuid(record_id)}"
        expected = {"name": relative_name, "type": "A", "content": address, "ttl": 60, "enabled": True}
        current = self.request("GET", path, dns=True, allow_missing=True)
        if current is not None:
            props = current.get("properties", {})
            if any(str(props.get(key, "")).rstrip(".") != str(expected[key]) for key in ("name", "type", "content")):
                raise IonosApiError("ionos_dns_record_ownership_conflict")
        else:
            # Never overwrite a pre-existing hostname, even if it points to the
            # same address. This automation only owns its random record UUID.
            for record in self.items(f"/zones/{valid_uuid(zone_id)}/records", dns=True):
                name = str(record.get("metadata", {}).get("fqdn") or (
                    str(record.get("properties", {}).get("name", "")) + "." + zone_name
                )).lower().rstrip(".")
                if name == hostname:
                    raise IonosApiError("ionos_dns_name_already_exists")
        # Official DNS PUT is create-or-update by caller-supplied UUID, so a
        # lost response can safely be retried without creating a second record.
        result = self.request("PUT", path, payload={"properties": expected}, dns=True)
        props = result.get("properties", {})
        fqdn = str(result.get("metadata", {}).get("fqdn") or hostname).lower().rstrip(".")
        if str(props.get("content")) != address or props.get("enabled") is not True or fqdn != hostname:
            raise IonosApiError("ionos_dns_record_not_confirmed")
