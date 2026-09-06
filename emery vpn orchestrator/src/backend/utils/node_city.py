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
    "saint-petersburg": "Санкт-Петербург",
    "novosibirsk": "Новосибирск",
    "yekaterinburg": "Екатеринбург",
    "ekb": "Екатеринбург",
    "kazan": "Казань",
    "frankfurt": "Frankfurt",
    "amsterdam": "Amsterdam",
    "helsinki": "Helsinki",
    "warsaw": "Warsaw",
    "stockholm": "Stockholm",
    "zurich": "Zurich",
    "geneva": "Geneva",
    "vienna": "Vienna",
    "prague": "Prague",
    "bucharest": "Bucharest",
    "sofia": "Sofia",
    "london": "London",
    "paris": "Paris",
    "madrid": "Madrid",
    "milan": "Milan",
    "rome": "Rome",
    "tallinn": "Tallinn",
    "riga": "Riga",
    "vilnius": "Vilnius",
    "oslo": "Oslo",
    "copenhagen": "Copenhagen",
    "dublin": "Dublin",
    "new-york": "New York",
    "los-angeles": "Los Angeles",
    "miami": "Miami",
    "chicago": "Chicago",
    "toronto": "Toronto",
    "montreal": "Montreal",
    "singapore": "Singapore",
    "tokyo": "Tokyo",
    "seoul": "Seoul",
    "hong-kong": "Hong Kong",
}


def _tokenize(value: str) -> list[str]:
    clean = re.sub(r"[^a-zA-Zа-яА-Я0-9-]+", " ", value).strip().lower()
    if not clean:
        return []
    return [token for token in clean.split(" ") if token]


def _humanize_region_code(region_code: str) -> str:
    code = (region_code or "").strip().lower()
    if not code or code == "unknown":
        return "Unknown"
    return " ".join(part.capitalize() for part in code.split("-") if part)


def normalize_node_city(node: VpnNode) -> str:
    candidates = [node.region_code or "", node.name or "", node.endpoint or ""]
    for candidate in candidates:
        normalized_candidate = candidate.strip().lower()
        if normalized_candidate in _CITY_BY_TOKEN:
            return _CITY_BY_TOKEN[normalized_candidate]
        for token in _tokenize(candidate):
            if token in _CITY_BY_TOKEN:
                return _CITY_BY_TOKEN[token]
    return _humanize_region_code(node.region_code)
