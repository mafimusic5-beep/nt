import logging
import re


class SecretMaskingFilter(logging.Filter):
    PATTERNS = [
        re.compile(r"(token=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(api[_-]?key=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(password=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(passwd=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(secret=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(access_key=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(username=)([^&\s,]+)", re.IGNORECASE),
        # Privacy identifiers: useful for authorization in memory, not useful
        # as durable log dimensions.
        re.compile(r"(telegram_id=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(user_id=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(device_id=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(device_fingerprint=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(fingerprint=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(client_ip=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(?<![A-Za-z0-9_])(ip=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(nonce=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(signature=)([^&\s,]+)", re.IGNORECASE),
        re.compile(r"(public_key=)([^&\s,]+)", re.IGNORECASE),
    ]
    SSH_KEY_PATTERN = re.compile(
        r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----",
        re.MULTILINE,
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        for pattern in self.PATTERNS:
            msg = pattern.sub(r"\1***REDACTED***", msg)
        msg = self.SSH_KEY_PATTERN.sub("***SSH_PRIVATE_KEY_REDACTED***", msg)
        record.msg = msg
        record.args = ()
        return True


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))
    root = logging.getLogger()
    privacy_filter = SecretMaskingFilter()

    # Child loggers propagate directly to ancestor handlers; logger-level
    # filters on the root are not applied to those records. Put the privacy
    # filter on every root handler so service/module loggers are covered too.
    for handler in root.handlers:
        handler.addFilter(privacy_filter)

    for noisy_logger in ("httpx", "httpcore", "paramiko", "paramiko.transport"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
