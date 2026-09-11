from __future__ import annotations

import re
import unicodedata


COUNTRY_NAME_RU_BY_CODE: dict[str, str] = {
    "AT": "Австрия",
    "BE": "Бельгия",
    "BG": "Болгария",
    "CA": "Канада",
    "CH": "Швейцария",
    "CZ": "Чехия",
    "DE": "Германия",
    "DK": "Дания",
    "EE": "Эстония",
    "ES": "Испания",
    "FI": "Финляндия",
    "FR": "Франция",
    "GB": "Великобритания",
    "GR": "Греция",
    "HK": "Гонконг",
    "HR": "Хорватия",
    "HU": "Венгрия",
    "IE": "Ирландия",
    "IL": "Израиль",
    "IS": "Исландия",
    "IT": "Италия",
    "JP": "Япония",
    "KZ": "Казахстан",
    "LT": "Литва",
    "LU": "Люксембург",
    "LV": "Латвия",
    "MD": "Молдова",
    "NL": "Нидерланды",
    "NO": "Норвегия",
    "PL": "Польша",
    "PT": "Португалия",
    "RO": "Румыния",
    "RS": "Сербия",
    "RU": "Россия",
    "SE": "Швеция",
    "SG": "Сингапур",
    "SI": "Словения",
    "SK": "Словакия",
    "TR": "Турция",
    "UA": "Украина",
    "UK": "Великобритания",
    "US": "США",
}


_CITY_NAME_RU_BY_KEY: dict[str, str] = {
    "amsterdam": "Амстердам",
    "ashburn": "Ашберн",
    "berlin": "Берлин",
    "chicago": "Чикаго",
    "dusseldorf": "Дюссельдорф",
    "frankfurt": "Франкфурт",
    "hamburg": "Гамбург",
    "helsinki": "Хельсинки",
    "istanbul": "Стамбул",
    "kleve": "Клеве",
    "london": "Лондон",
    "los angeles": "Лос-Анджелес",
    "madrid": "Мадрид",
    "miami": "Майами",
    "milan": "Милан",
    "moscow": "Москва",
    "munich": "Мюнхен",
    "new york": "Нью-Йорк",
    "nuremberg": "Нюрнберг",
    "paris": "Париж",
    "prague": "Прага",
    "rotterdam": "Роттердам",
    "saint petersburg": "Санкт-Петербург",
    "singapore": "Сингапур",
    "stockholm": "Стокгольм",
    "strasbourg": "Страсбург",
    "vienna": "Вена",
    "warsaw": "Варшава",
    "zurich": "Цюрих",
}


_MULTI_TRANSLIT: tuple[tuple[str, str], ...] = (
    ("shch", "щ"),
    ("sch", "ш"),
    ("zh", "ж"),
    ("kh", "х"),
    ("ch", "ч"),
    ("sh", "ш"),
    ("ts", "ц"),
    ("ya", "я"),
    ("yu", "ю"),
    ("yo", "ё"),
    ("ye", "е"),
    ("ph", "ф"),
    ("th", "т"),
    ("ck", "к"),
)

_CHAR_TRANSLIT: dict[str, str] = {
    "a": "а",
    "b": "б",
    "c": "к",
    "d": "д",
    "e": "е",
    "f": "ф",
    "g": "г",
    "h": "х",
    "i": "и",
    "j": "й",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "q": "к",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "v": "в",
    "w": "в",
    "x": "кс",
    "y": "и",
    "z": "з",
}


def _ascii_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).strip()


def _contains_cyrillic(value: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", value))


def russian_city_name(city: str) -> str:
    value = (city or "").strip()
    if not value:
        return ""
    if _contains_cyrillic(value):
        return value

    key = _ascii_key(value)
    if key in _CITY_NAME_RU_BY_KEY:
        return _CITY_NAME_RU_BY_KEY[key]

    source = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    lower = source.casefold()
    out: list[str] = []
    index = 0
    while index < len(lower):
        matched = False
        for latin, cyrillic in _MULTI_TRANSLIT:
            if lower.startswith(latin, index):
                out.append(cyrillic)
                index += len(latin)
                matched = True
                break
        if matched:
            continue
        char = lower[index]
        out.append(_CHAR_TRANSLIT.get(char, char))
        index += 1

    text = "".join(out)
    return re.sub(
        r"(^|[\s-])([а-яё])",
        lambda match: match.group(1) + match.group(2).upper(),
        text,
    )


def russian_location_label(
    country_code: str,
    city: str = "",
    *,
    fallback_country: str = "",
) -> str:
    code = (country_code or "").strip().upper()
    country = COUNTRY_NAME_RU_BY_CODE.get(code)
    if not country:
        raw_fallback = (fallback_country or "").strip()
        country = raw_fallback if _contains_cyrillic(raw_fallback) else (code or "Регион")

    city_ru = russian_city_name(city)
    if city_ru and city_ru.casefold() != country.casefold():
        return f"{city_ru}, {country}"
    return country
