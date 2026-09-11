from __future__ import annotations

import re

from src.common.location_labels import COUNTRY_NAME_RU_BY_CODE, russian_location_label
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


def _location_from_region_code(region_code: str) -> str:
    normalized = region_code.strip().lower()
    if not normalized:
        return ""

    country, separator, city_slug = normalized.partition("-")
    country_code = country.upper()
    if country_code not in COUNTRY_NAME_RU_BY_CODE:
        return ""

    city = city_slug.replace("-", " ").strip() if separator else ""
    return russian_location_label(country_code, city)


def _city_from_node_name(name: str) -> str:
    # Historical bot-created names can look like: "VPS-506295 (Singapore)".
    match = re.search(r"\(([^()]{2,64})\)\s*$", name.strip())
    return match.group(1).strip() if match else ""


def normalize_node_city(node: VpnNode) -> str:
    # New auto-setup region codes carry both country and city (for example
    # de-kleve). Prefer that stable machine metadata and expose a Russian label
    # such as "Германия Клеве" to clients.
    from_region = _location_from_region_code(node.region_code or "")
    if from_region:
        return from_region

    candidates = [node.region_code or "", node.name or "", node.endpoint or ""]
    for candidate in candidates:
        for token in _tokenize(candidate):
            if token in _CITY_BY_TOKEN:
                return _CITY_BY_TOKEN[token]

    # If the bootstrap already stored a Russian human label, preserve it.
    name = (node.name or "").strip()
    if re.search(r"[А-Яа-яЁё]", name):
        return name

    from_name = _city_from_node_name(name)
    if from_name:
        return from_name

    if node.region_code:
        return _title_from_slug(node.region_code)

    return "Неизвестный регион"
