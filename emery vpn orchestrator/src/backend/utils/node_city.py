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


def _tokenize(value: str) -> list[str]:
    clean = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", " ", value).strip().lower()
    if not clean:
        return []
    return [token for token in clean.split(" ") if token]


def normalize_node_city(node: VpnNode) -> str:
    candidates = [node.region_code or "", node.name or "", node.endpoint or ""]
    for candidate in candidates:
        for token in _tokenize(candidate):
            if token in _CITY_BY_TOKEN:
                return _CITY_BY_TOKEN[token]
    return "Unknown"
