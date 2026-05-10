from __future__ import annotations

import re

from src.common.models import VpnNode

_CITY_BY_TOKEN: dict[str, str] = {
    "moscow": "Москва",
    "msk": "Москва",
    "mos": "Москва",
    "spb": "Санкт-Петербург",
    "piter": "Санкт-Петербург",
    "saint petersburg": "Санкт-Петербург",
    "novosibirsk": "Новосибирск",
    "yekaterinburg": "Екатеринбург",
    "ekb": "Екатеринбург",
    "kazan": "Казань",
}

_COUNTRY_BY_CODE: dict[str, str] = {
    "de": "Germany",
    "fi": "Finland",
    "fr": "France",
    "gb": "United Kingdom",
    "hk": "Hong Kong",
    "jp": "Japan",
    "kz": "Kazakhstan",
    "nl": "Netherlands",
    "pl": "Poland",
    "ru": "Russia",
    "se": "Sweden",
    "sg": "Singapore",
    "tr": "Turkey",
    "ua": "Ukraine",
    "us": "United States",
}


def _tokenize(value: str) -> list[str]:
    clean = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", " ", value).strip().lower()
    if not clean:
        return []
    return [token for token in clean.split(" ") if token]


def _title_from_slug(value: str) -> str:
    words = [word for word in re.split(r"[-_\s]+", value.strip()) if word]
    if not words:
        return ""
    return " ".join(word[:1].upper() + word[1:] for word in words)


def _city_from_region_code(region_code: str) -> str:
    normalized = region_code.strip().lower()
    if not normalized:
        return ""
    if normalized in _COUNTRY_BY_CODE:
        return _COUNTRY_BY_CODE[normalized]
    if "-" not in normalized:
        return _title_from_slug(normalized)
    country, city_slug = normalized.split("-", 1)
    if city_slug:
        return _title_from_slug(city_slug)
    return _COUNTRY_BY_CODE.get(country, country.upper())


def _city_from_node_name(name: str) -> str:
    # Bot-created names look like: "VPS-506295 (Singapore)".
    match = re.search(r"\(([^()]{2,64})\)\s*$", name.strip())
    return match.group(1).strip() if match else ""


def normalize_node_city(node: VpnNode) -> str:
    candidates = [node.region_code or "", node.name or "", node.endpoint or ""]
    for candidate in candidates:
        for token in _tokenize(candidate):
            if token in _CITY_BY_TOKEN:
                return _CITY_BY_TOKEN[token]

    from_name = _city_from_node_name(node.name or "")
    if from_name:
        return from_name

    from_region = _city_from_region_code(node.region_code or "")
    if from_region:
        return from_region

    return "Unknown"
