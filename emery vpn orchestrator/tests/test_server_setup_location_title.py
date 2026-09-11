from src.bot.handlers.server_setup import _auto_location_title


def test_auto_location_title_uses_country_and_city() -> None:
    assert _auto_location_title(
        {
            "country_code": "DE",
            "region_code": "de-kleve",
            "region_name": "Kleve",
        }
    ) == "In Germany Kleve"


def test_auto_location_title_does_not_repeat_country_without_city() -> None:
    assert _auto_location_title(
        {
            "country_code": "DE",
            "region_code": "de",
            "region_name": "Germany",
        }
    ) == "In Germany"
