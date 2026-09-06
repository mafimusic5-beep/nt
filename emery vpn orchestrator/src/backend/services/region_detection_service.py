from __future__ import annotations

import ipaddress
import logging
import re
import socket
import unicodedata
from dataclasses import dataclass
from typing import Any

import httpx

from src.common.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DetectedRegion:
    code: str
    name: str
    country: str = ""
    source: str = "unknown"
    confidence: float = 0.0


_CITY_ALIASES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("moscow", "москва", "msk"), "moscow", "Москва"),
    (("saint petersburg", "st petersburg", "st. petersburg", "санкт петербург", "spb"), "saint-petersburg", "Санкт-Петербург"),
    (("frankfurt", "frankfurt am main", "fra"), "frankfurt", "Frankfurt"),
    (("amsterdam", "ams"), "amsterdam", "Amsterdam"),
    (("helsinki", "hel"), "helsinki", "Helsinki"),
    (("warsaw", "warszawa", "waw"), "warsaw", "Warsaw"),
    (("stockholm", "sto"), "stockholm", "Stockholm"),
    (("zurich", "zürich", "zrh"), "zurich", "Zurich"),
    (("geneva", "genève", "gva"), "geneva", "Geneva"),
    (("vienna", "wien", "vie"), "vienna", "Vienna"),
    (("prague", "praha", "prg"), "prague", "Prague"),
    (("bucharest", "bucuresti", "otp"), "bucharest", "Bucharest"),
    (("sofia", "sof"), "sofia", "Sofia"),
    (("london", "lon"), "london", "London"),
    (("paris", "par"), "paris", "Paris"),
    (("madrid", "mad"), "madrid", "Madrid"),
    (("milan", "milano", "mxp"), "milan", "Milan"),
    (("rome", "roma", "rom"), "rome", "Rome"),
    (("tallinn", "tll"), "tallinn", "Tallinn"),
    (("riga", "rix"), "riga", "Riga"),
    (("vilnius", "vno"), "vilnius", "Vilnius"),
    (("oslo", "osl"), "oslo", "Oslo"),
    (("copenhagen", "københavn", "cph"), "copenhagen", "Copenhagen"),
    (("dublin", "dub"), "dublin", "Dublin"),
    (("new york", "nyc"), "new-york", "New York"),
    (("los angeles", "lax"), "los-angeles", "Los Angeles"),
    (("miami", "mia"), "miami", "Miami"),
    (("chicago", "chi"), "chicago", "Chicago"),
    (("toronto", "yyz"), "toronto", "Toronto"),
    (("montreal", "montréal", "yul"), "montreal", "Montreal"),
    (("singapore", "sin"), "singapore", "Singapore"),
    (("tokyo", "tyo", "nrt"), "tokyo", "Tokyo"),
    (("seoul", "sel", "icn"), "seoul", "Seoul"),
    (("hong kong", "hkg"), "hong-kong", "Hong Kong"),
)


def _normalize_text(value: str) -> str:
    lowered = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _slugify(value: str, max_length: int = 16) -> str:
    normalized = _normalize_text(value).replace(" ", "-")
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized[:max_length].rstrip("-") or "unknown"


class RegionDetectionService:
    """Resolve a VPN node to a stable region pool without manual region input.

    Provider metadata is preferred. Public-IP geolocation is a fallback. A failed
    lookup never guesses a pool: callers should keep the node out of active pools.
    """

    def detect(
        self,
        *,
        endpoint: str,
        provider_metadata: dict[str, Any] | None = None,
        configured_datacenter: str = "",
    ) -> DetectedRegion | None:
        if not settings.node_region_autodetect_enabled:
            return None

        metadata_text = self._metadata_text(provider_metadata or {})
        provider_hit = self._from_text(metadata_text, source="provider_metadata", confidence=0.98)
        if provider_hit:
            return provider_hit

        datacenter_hit = self._from_text(configured_datacenter, source="provider_datacenter", confidence=0.95)
        if datacenter_hit:
            return datacenter_hit

        host_hit = self._from_text(endpoint, source="endpoint_name", confidence=0.70)
        if host_hit:
            return host_hit

        ip = self._resolve_public_ip(endpoint)
        if not ip:
            logger.warning("region detection skipped: endpoint=%s has no public IP", endpoint)
            return None

        return self._from_ip_geo(ip)

    @staticmethod
    def _metadata_text(metadata: dict[str, Any]) -> str:
        preferred_keys = {
            "city",
            "location",
            "datacenter",
            "data_center",
            "dc",
            "region",
            "region_name",
            "country",
            "country_code",
            "name",
        }
        values: list[str] = []
        for key, value in metadata.items():
            if str(key).lower() not in preferred_keys:
                continue
            if isinstance(value, dict):
                values.extend(str(v) for v in value.values() if isinstance(v, (str, int, float)))
            elif isinstance(value, (str, int, float)):
                values.append(str(value))
        return " ".join(values)

    @staticmethod
    def _from_text(value: str, *, source: str, confidence: float) -> DetectedRegion | None:
        if not value:
            return None
        normalized = f" {_normalize_text(value)} "
        for aliases, code, name in _CITY_ALIASES:
            for alias in aliases:
                needle = _normalize_text(alias)
                if needle and re.search(rf"(?:^|\s){re.escape(needle)}(?:\s|$)", normalized.strip()):
                    return DetectedRegion(code=code, name=name, source=source, confidence=confidence)
        return None

    @staticmethod
    def _resolve_public_ip(endpoint: str) -> str | None:
        host = (endpoint or "").strip()
        if not host:
            return None
        if "://" in host:
            host = host.split("://", 1)[1]
        host = host.split("/", 1)[0]
        if host.startswith("[") and "]" in host:
            host = host[1 : host.index("]")]
        elif host.count(":") == 1:
            host = host.split(":", 1)[0]

        try:
            candidates = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                candidates = list({row[4][0] for row in socket.getaddrinfo(host, None)})
            except socket.gaierror:
                return None

        for candidate in candidates:
            try:
                ip = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if ip.is_global:
                return str(ip)
        return None

    def _from_ip_geo(self, ip: str) -> DetectedRegion | None:
        url = settings.node_region_geo_url_template.format(ip=ip)
        try:
            with httpx.Client(timeout=settings.node_region_geo_timeout_seconds) as client:
                response = client.get(url, headers={"User-Agent": "skryon-region-detector/1.0"})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, KeyError):
            logger.warning("region geolocation failed for endpoint IP", exc_info=True)
            return None

        if not isinstance(payload, dict):
            return None
        if payload.get("success") is False or str(payload.get("status", "")).lower() == "fail":
            return None

        city = str(payload.get("city") or payload.get("regionName") or payload.get("region") or "").strip()
        country = str(payload.get("country") or "").strip()
        country_code = str(payload.get("country_code") or payload.get("countryCode") or "").strip().upper()
        if not city:
            return None

        known = self._from_text(city, source="ip_geolocation", confidence=0.85)
        if known:
            return DetectedRegion(
                code=known.code,
                name=known.name,
                country=country or country_code,
                source=known.source,
                confidence=known.confidence,
            )

        code = _slugify(city)
        if code == "unknown":
            return None
        return DetectedRegion(
            code=code,
            name=city,
            country=country or country_code,
            source="ip_geolocation",
            confidence=0.80,
        )
