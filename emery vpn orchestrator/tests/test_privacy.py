from starlette.requests import Request

from src.backend.middleware.rate_limit import _privacy_key
from src.backend.utils.privacy import sanitize_device_name


def _request(client_ip: str, device_id: str = "installation-123") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/v1/profile",
        "raw_path": b"/api/v1/profile",
        "query_string": b"",
        "headers": [(b"x-emery-device-id", device_id.encode("utf-8"))],
        "client": (client_ip, 12345),
        "server": ("vpn.example.com", 443),
    }
    return Request(scope)


def test_technical_device_names_are_replaced() -> None:
    assert sanitize_device_name("Google sdk_gphone64_x86_64", "android") == "Android-устройство"
    assert sanitize_device_name("", "android") == "Android-устройство"
    assert sanitize_device_name("Мой телефон", "android") == "Мой телефон"


def test_rate_limit_key_does_not_depend_on_client_ip() -> None:
    first = _privacy_key(_request("203.0.113.10"))
    second = _privacy_key(_request("198.51.100.20"))
    assert first == second
    assert "203.0.113.10" not in first
    assert "198.51.100.20" not in second


def test_rate_limit_key_changes_for_another_installation() -> None:
    assert _privacy_key(_request("203.0.113.10", "installation-a")) != _privacy_key(
        _request("203.0.113.10", "installation-b")
    )
