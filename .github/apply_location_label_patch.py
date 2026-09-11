from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"{label}: expected marker not found")
    return text.replace(old, new, 1)


server_path = Path("emery vpn orchestrator/src/bot/handlers/server_setup.py")
text = server_path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "import logging\nimport shlex\n",
    "import logging\nimport re\nimport shlex\nimport unicodedata\n",
    "server_setup imports",
)

start = text.index("COUNTRY_NAME_EN_BY_CODE: dict[str, str] = {")
end = text.index("\n\ndef _command_args", start)
helpers = '''CITY_NAME_RU_OVERRIDES: dict[str, str] = {
    "amsterdam": "Амстердам",
    "barcelona": "Барселона",
    "berlin": "Берлин",
    "bucharest": "Бухарест",
    "budapest": "Будапешт",
    "chisinau": "Кишинёв",
    "cologne": "Кёльн",
    "dusseldorf": "Дюссельдорф",
    "düsseldorf": "Дюссельдорф",
    "falkenstein": "Фалькенштайн",
    "frankfurt": "Франкфурт",
    "frankfurt am main": "Франкфурт-на-Майне",
    "helsinki": "Хельсинки",
    "hong kong": "Гонконг",
    "istanbul": "Стамбул",
    "kleve": "Клеве",
    "kiev": "Киев",
    "kyiv": "Киев",
    "london": "Лондон",
    "madrid": "Мадрид",
    "milan": "Милан",
    "moscow": "Москва",
    "munich": "Мюнхен",
    "new york": "Нью-Йорк",
    "nuremberg": "Нюрнберг",
    "paris": "Париж",
    "prague": "Прага",
    "riga": "Рига",
    "rome": "Рим",
    "rotterdam": "Роттердам",
    "saint petersburg": "Санкт-Петербург",
    "san francisco": "Сан-Франциско",
    "singapore": "Сингапур",
    "sofia": "София",
    "stockholm": "Стокгольм",
    "tallinn": "Таллин",
    "tokyo": "Токио",
    "vienna": "Вена",
    "vilnius": "Вильнюс",
    "warsaw": "Варшава",
}

_TRANSLIT_MULTI: tuple[tuple[str, str], ...] = (
    ("shch", "щ"),
    ("tsch", "ч"),
    ("sch", "ш"),
    ("zh", "ж"),
    ("kh", "х"),
    ("ch", "ч"),
    ("sh", "ш"),
    ("ts", "ц"),
    ("ya", "я"),
    ("yo", "ё"),
    ("yu", "ю"),
    ("ye", "е"),
    ("ph", "ф"),
    ("th", "т"),
    ("ck", "к"),
)

_TRANSLIT_SINGLE: dict[str, str] = {
    "a": "а",
    "b": "б",
    "c": "к",
    "d": "д",
    "e": "е",
    "f": "ф",
    "g": "г",
    "h": "х",
    "i": "и",
    "j": "дж",
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
    "y": "й",
    "z": "з",
}


def _country_flag(country_code: str) -> str:
    code = (country_code or "").strip().upper()
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        return ""
    base = 0x1F1E6
    return "".join(chr(base + ord(char) - ord("A")) for char in code)


def _city_name_ru(city: str) -> str:
    value = (city or "").strip()
    if not value:
        return ""
    if re.search(r"[А-Яа-яЁё]", value):
        return value

    normalized_key = re.sub(r"\\s+", " ", value.casefold()).strip()
    override = CITY_NAME_RU_OVERRIDES.get(normalized_key)
    if override:
        return override

    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    result: list[str] = []
    index = 0
    while index < len(ascii_value):
        matched = False
        for latin, cyrillic in _TRANSLIT_MULTI:
            if ascii_value.startswith(latin, index):
                result.append(cyrillic)
                index += len(latin)
                matched = True
                break
        if matched:
            continue

        char = ascii_value[index]
        result.append(_TRANSLIT_SINGLE.get(char, char))
        index += 1

    transliterated = "".join(result).strip()
    if not transliterated:
        return value
    return " ".join(
        part[:1].upper() + part[1:] if part else part
        for part in transliterated.split(" ")
    )
'''
text = text[:start] + helpers + text[end:]

start = text.index("def _auto_location_title(location: dict | None) -> str:")
end = text.index("\n\n@router.message", start)
title_function = '''def _auto_location_title(location: dict | None) -> str:
    if not location:
        return ""

    code = str(location.get("country_code") or "").strip().upper()
    region_code = str(location.get("region_code") or "").strip().lower()
    region_name = str(location.get("region_name") or "").strip()

    # A city result has a country-city region code (for example de-kleve).
    # If GeoIP only knows the country, do not publish a country as a fake city.
    if "-" not in region_code or not region_name:
        return ""

    flag = _country_flag(code)
    city = _city_name_ru(region_name)
    if not flag or not city:
        return ""
    return f"{flag} {city}"
'''
text = text[:start] + title_function + text[end:]
text = replace_once(
    text,
    '        f"Регион: {node.get(\'region_code\')}\\n"\n',
    "",
    "server_setup admin region line",
)
server_path.write_text(text, encoding="utf-8")

test_path = Path("emery vpn orchestrator/tests/test_server_setup_location_title.py")
test_path.write_text(
    '''from src.bot.handlers.server_setup import _auto_location_title, _city_name_ru, _country_flag


def test_auto_location_title_uses_flag_and_russian_city_only() -> None:
    assert _auto_location_title(
        {
            "country_code": "DE",
            "region_code": "de-kleve",
            "region_name": "Kleve",
        }
    ) == "🇩🇪 Клеве"


def test_auto_location_title_requires_city() -> None:
    assert _auto_location_title(
        {
            "country_code": "DE",
            "region_code": "de",
            "region_name": "Germany",
        }
    ) == ""


def test_auto_location_title_has_no_unknown_fallback() -> None:
    assert _auto_location_title(None) == ""


def test_city_name_has_generic_cyrillic_fallback() -> None:
    assert _city_name_ru("Limburg") == "Лимбург"


def test_country_flag_uses_iso_country_code() -> None:
    assert _country_flag("DE") == "🇩🇪"
''',
    encoding="utf-8",
)

models_path = Path("app/src/main/java/com/v2ray/ang/ui/premium/vpn/VpnUiModels.kt")
models = models_path.read_text(encoding="utf-8")
models = replace_once(
    models,
    '''                decodedFragment.startsWith("In ", ignoreCase = true) ||
                isRussianLocationLabel(decodedFragment)
''',
    '''                decodedFragment.startsWith("In ", ignoreCase = true) ||
                isRussianLocationLabel(decodedFragment) ||
                countryCodeFromFlag(decodedFragment).isNotBlank()
''',
    "VpnUiModels source label",
)
models = replace_once(
    models,
    '''        val value = sourceLocationLabel()
        if (value.startsWith("In ", ignoreCase = true)) return value
''',
    '''        val value = sourceLocationLabel()
        if (countryCodeFromFlag(value).isNotBlank()) return stripLeadingFlag(value)
        if (value.startsWith("In ", ignoreCase = true)) return value
''',
    "VpnUiModels city label",
)
models = replace_once(
    models,
    '''    fun countryCodeLabel(): String {
        val value = sourceLocationLabel()
            .lowercase()
''',
    '''    fun countryCodeLabel(): String {
        val sourceValue = sourceLocationLabel()
        val flagCode = countryCodeFromFlag(sourceValue)
        if (flagCode.isNotBlank()) return flagCode

        val value = sourceValue
            .lowercase()
''',
    "VpnUiModels country code",
)
marker = "private fun isRussianLocationLabel(value: String): Boolean {"
if marker not in models:
    raise RuntimeError("VpnUiModels helper insertion marker not found")
flag_helpers = '''private fun countryCodeFromFlag(value: String): String {
    val text = value.trim()
    if (text.isEmpty()) return ""

    val first = Character.codePointAt(text, 0)
    val firstLength = Character.charCount(first)
    if (text.length <= firstLength) return ""

    val second = Character.codePointAt(text, firstLength)
    val base = 0x1F1E6
    if (first !in base..(base + 25) || second !in base..(base + 25)) return ""

    val firstLetter = ('A'.code + (first - base)).toChar()
    val secondLetter = ('A'.code + (second - base)).toChar()
    return "$firstLetter$secondLetter"
}

private fun stripLeadingFlag(value: String): String {
    val text = value.trim()
    if (countryCodeFromFlag(text).isBlank()) return text

    val first = Character.codePointAt(text, 0)
    val firstLength = Character.charCount(first)
    val second = Character.codePointAt(text, firstLength)
    val secondLength = Character.charCount(second)
    return text
        .substring(firstLength + secondLength)
        .trimStart()
        .trimStart('•', '·', '-', '—', '|')
        .trimStart()
}

'''
models = models.replace(marker, flag_helpers + marker, 1)
models_path.write_text(models, encoding="utf-8")

screen_path = Path("app/src/main/java/com/v2ray/ang/ui/premium/vpn/VpnMainScreen.kt")
screen = screen_path.read_text(encoding="utf-8")
screen = replace_once(
    screen,
    'text = "$selectedTitle • $selectedCode"',
    "text = selectedTitle",
    "VpnMainScreen selected title",
)
screen = replace_once(
    screen,
    'text = "Регион"',
    'text = "Сервер"',
    "VpnMainScreen selector caption",
)
screen = replace_once(
    screen,
    '''                                Text(
                                    text = code,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = AppUiColors.TextSecondary,
                                    maxLines = 1,
                                )
''',
    "",
    "VpnMainScreen dropdown country code",
)
screen = replace_once(
    screen,
    '''            Spacer(Modifier.height(2.dp))
            Text(
                text = code,
                style = MaterialTheme.typography.bodySmall,
                color = AppUiColors.TextSecondary,
                maxLines = 1,
            )
''',
    "",
    "VpnMainScreen chip country code",
)

flag_start = screen.index("@Composable\nprivate fun FlagMark(code: String, modifier: Modifier = Modifier) {")
flag_end = screen.index("\n\n@Composable\nprivate fun AutoConnectCard", flag_start)
flag_function = '''@Composable
private fun FlagMark(code: String, modifier: Modifier = Modifier) {
    Box(modifier = modifier, contentAlignment = Alignment.Center) {
        Text(
            text = countryFlagEmoji(code),
            style = MaterialTheme.typography.bodyLarge,
            maxLines = 1,
        )
    }
}

private fun countryFlagEmoji(code: String): String {
    val normalized = code.trim().uppercase().let { if (it == "UK") "GB" else it }
    if (normalized.length != 2 || normalized.any { it !in 'A'..'Z' }) return "🌐"
    val base = 0x1F1E6
    return normalized.map { letter ->
        String(Character.toChars(base + (letter.code - 'A'.code)))
    }.joinToString("")
}
'''
screen = screen[:flag_start] + flag_function + screen[flag_end:]
screen_path.write_text(screen, encoding="utf-8")

Path(".github/workflows/apply-location-label-patch.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
