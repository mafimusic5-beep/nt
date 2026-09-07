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
    "at": "Австрия",
    "be": "Бельгия",
    "bg": "Болгария",
    "ca": "Канада",
    "ch": "Швейцария",
    "cz": "Чехия",
    "de": "Германия",
    "dk": "Дания",
    "es": "Испания",
    "fi": "Финляндия",
    "fr": "Франция",
    "gb": "Великобритания",
    "hk": "Гонконг",
    "hr": "Хорватия",
    "it": "Италия",
    "jp": "Япония",
    "kz": "Казахстан",
    "nl": "Нидерланды",
    "no": "Норвегия",
    "pl": "Польша",
    "pt": "Португалия",
    "ro": "Румыния",
    "rs": "Сербия",
    "ru": "Россия",
    "se": "Швеция",
    "sg": "Сингапур",
    "tr": "Турция",
    "ua": "Украина",
    "us": "США",
}

_COUNTRY_CODE_ALIASES = {
    "uk": "gb",
    "usa": "us",
}

_COUNTRY_NAME_TO_CODE: dict[str, str] = {
    "austria": "at",
    "belgium": "be",
    "bulgaria": "bg",
    "canada": "ca",
    "switzerland": "ch",
    "czechia": "cz",
    "czech republic": "cz",
    "germany": "de",
    "deutschland": "de",
    "denmark": "dk",
    "spain": "es",
    "finland": "fi",
    "france": "fr",
    "united kingdom": "gb",
    "great britain": "gb",
    "hong kong": "hk",
    "croatia": "hr",
    "italy": "it",
    "japan": "jp",
    "kazakhstan": "kz",
    "netherlands": "nl",
    "nederland": "nl",
    "norway": "no",
    "poland": "pl",
    "portugal": "pt",
    "romania": "ro",
    "serbia": "rs",
    "russia": "ru",
    "sweden": "se",
    "singapore": "sg",
    "turkey": "tr",
    "turkiye": "tr",
    "ukraine": "ua",
    "united states": "us",
    "united states of america": "us",
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


def normalize_pool_region_code(region_code: str) -> str:
    """Collapse country-city codes to one country pool.

    Existing nodes may have codes like ``de-frankfurt`` from older GeoIP setup.
    They still belong to the same ``de`` pool as newly provisioned German nodes.
    Non-country legacy region identifiers are preserved unchanged.
    """
    normalized = str(region_code or "").strip().lower()
    if not normalized:
        return ""
    if normalized == "auto":
        return "auto"
    head = re.split(r"[-_]", normalized, maxsplit=1)[0]
    head = _COUNTRY_CODE_ALIASES.get(head, head)
    if head in _COUNTRY_BY_CODE:
        return head
    return normalized


def region_display_name(region_code: str, fallback: str = "") -> str:
    normalized = str(region_code or "").strip().lower()
    if not normalized:
        return fallback

    canonical = normalize_pool_region_code(normalized)
    if canonical in _COUNTRY_BY_CODE:
        return _COUNTRY_BY_CODE[canonical]

    direct_country = _COUNTRY_NAME_TO_CODE.get(normalized)
    if direct_country:
        return _COUNTRY_BY_CODE[direct_country]

    if canonical in _CITY_BY_TOKEN:
        return _CITY_BY_TOKEN[canonical]
    return fallback or _title_from_slug(canonical)


def _city_from_node_name(name: str) -> str:
    # Bot-created names may look like: "VPS-506295 (Singapore)".
    match = re.search(r"\(([^()]{2,64})\)\s*$", name.strip())
    return match.group(1).strip() if match else ""


def normalize_node_city(node: VpnNode) -> str:
    # Region code is authoritative for the public pool. Country-city codes from
    # older nodes are deliberately displayed as the country pool name.
    from_region = region_display_name(node.region_code or "")
    if from_region and from_region.casefold() != "auto":
        return from_region

    candidates = [node.name or "", node.endpoint or ""]
    for candidate in candidates:
        lowered = re.sub(r"[_\-]+", " ", candidate).strip().lower()
        country_code = _COUNTRY_NAME_TO_CODE.get(lowered)
        if country_code:
            return _COUNTRY_BY_CODE[country_code]
        for token in _tokenize(candidate):
            token_code = _COUNTRY_CODE_ALIASES.get(token, token)
            if token_code in _COUNTRY_BY_CODE:
                return _COUNTRY_BY_CODE[token_code]
            if token in _CITY_BY_TOKEN:
                return _CITY_BY_TOKEN[token]

    from_name = _city_from_node_name(node.name or "")
    if from_name:
        country_code = _COUNTRY_NAME_TO_CODE.get(from_name.casefold())
        if country_code:
            return _COUNTRY_BY_CODE[country_code]
        return from_name

    return "Неизвестный регион"
