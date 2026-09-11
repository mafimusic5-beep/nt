from src.bot.handlers.server_setup import _auto_location_title, _city_name_ru, _country_flag


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
