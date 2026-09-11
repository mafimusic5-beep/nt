from src.bot.utils.geoip import _parse_ipapi, _parse_ipinfo_is, _parse_ipwho


IP = "217.154.243.155"
EXPECTED = {
    "ip": IP,
    "country_code": "DE",
    "region_code": "de-kleve",
    "region_name": "Kleve",
}


def test_ipapi_payload_normalizes_country_and_city() -> None:
    assert _parse_ipapi(
        IP,
        {"country_code": "DE", "country_name": "Germany", "city": "Kleve"},
    ) == EXPECTED


def test_ipwho_payload_normalizes_country_and_city() -> None:
    assert _parse_ipwho(
        IP,
        {"success": True, "country_code": "DE", "country": "Germany", "city": "Kleve"},
    ) == EXPECTED


def test_ipinfo_is_payload_normalizes_country_and_city() -> None:
    assert _parse_ipinfo_is(
        IP,
        {
            "country": {"short_name": "DE", "long_name": "Germany"},
            "city": "Kleve",
        },
    ) == EXPECTED
