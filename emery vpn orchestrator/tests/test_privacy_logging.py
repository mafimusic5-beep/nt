import logging

from src.backend.core.logging import SecretMaskingFilter


def _filtered_message(message: str, *args) -> str:
    record = logging.LogRecord(
        name="privacy-test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )
    assert SecretMaskingFilter().filter(record) is True
    return record.getMessage()


def test_user_identifiers_are_redacted():
    message = _filtered_message(
        "redeem succeeded: telegram_id=%s user_id=%s device_id=%s client_ip=%s",
        123456789,
        42,
        "dp1:secret-device",
        "203.0.113.9",
    )
    assert "123456789" not in message
    assert "dp1:secret-device" not in message
    assert "203.0.113.9" not in message
    assert message.count("***REDACTED***") == 4


def test_auth_material_is_redacted():
    message = _filtered_message(
        "request access_key=ABC nonce=123 signature=xyz public_key=pk token=tok"
    )
    for secret in ("ABC", "123", "xyz", "pk", "tok"):
        assert secret not in message
