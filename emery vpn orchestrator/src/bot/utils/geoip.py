from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import unicodedata
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()


def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _resolve_public_ip(endpoint: str) -> str | None:
    host = endpoint.strip().strip("[]")
    if not host:
        return None
    try:
        parsed = ipaddress.ip_address(host)
        return str(parsed) if _is_public_ip(parsed) else None
    except ValueError:
        pass

    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
    except OSError:
        return None

    for family, _, _, _, sockaddr in infos:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        try:
            parsed = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _is_public_ip(parsed):
            return str(parsed)
    return None


def _build_location(ip: str, country_code: str, city: str, country_name: str) -> dict | None:
    code = (country_code or "").strip().upper()
    city = (city or "").strip()
    country_name = (country_name or "").strip()
    if len(code) != 2:
        return None

    city_slug = _slugify(city)
    if city_slug:
        region_code = f"{code.lower()}-{city_slug}"[:64]
        region_name = city
    else:
        region_code = code.lower()
        region_name = country_name or code

    return {
        "ip": ip,
        "country_code": code,
        "region_code": region_code,
        "region_name": region_name,
    }


def _parse_ipapi(ip: str, payload: Any) -> dict | None:
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    return _build_location(
        ip,
        str(payload.get("country_code") or payload.get("country") or ""),
        str(payload.get("city") or ""),
        str(payload.get("country_name") or ""),
    )


def _parse_ipwho(ip: str, payload: Any) -> dict | None:
    if not isinstance(payload, dict) or payload.get("success") is False:
        return None
    return _build_location(
        ip,
        str(payload.get("country_code") or ""),
        str(payload.get("city") or ""),
        str(payload.get("country") or ""),
    )


def _parse_ipinfo_is(ip: str, payload: Any) -> dict | None:
    if not isinstance(payload, dict):
        return None
    country = payload.get("country") or {}
    if not isinstance(country, dict):
        country = {}
    return _build_location(
        ip,
        str(country.get("short_name") or payload.get("country_code") or ""),
        str(payload.get("city") or ""),
        str(country.get("long_name") or payload.get("country_name") or ""),
    )


async def detect_node_location(endpoint: str) -> dict | None:
    """Resolve VPS country/city with independent GeoIP fallbacks.

    A provisioning run must not depend on one third-party GeoIP service. The
    first valid country result wins; city is included when the provider has it.
    """
    ip = await _resolve_public_ip(endpoint)
    if not ip:
        return None

    providers: tuple[tuple[str, str, Callable[[str, Any], dict | None]], ...] = (
        ("ipapi.co", f"https://ipapi.co/{ip}/json/", _parse_ipapi),
        ("ipwho.is", f"https://ipwho.is/{ip}", _parse_ipwho),
        ("ipinfo.is", f"https://ipinfo.is/{ip}", _parse_ipinfo_is),
    )

    headers = {"User-Agent": "Skryon-GeoIP/1.0"}
    async with httpx.AsyncClient(timeout=4.0, follow_redirects=True, headers=headers) as http:
        for provider, url, parser in providers:
            try:
                response = await http.get(url)
                response.raise_for_status()
                location = parser(ip, response.json())
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "geoip provider failed: provider=%s endpoint=%s ip=%s err=%s",
                    provider,
                    endpoint,
                    ip,
                    exc,
                )
                continue
            if location:
                logger.info(
                    "geoip resolved: provider=%s ip=%s region=%s",
                    provider,
                    ip,
                    location.get("region_code"),
                )
                return location

    logger.warning("all geoip providers failed: endpoint=%s ip=%s", endpoint, ip)
    return None
