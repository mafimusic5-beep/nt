import logging
import re


class SecretMaskingFilter(logging.Filter):
    PATTERNS = [
        re.compile(r"(token=)([^&\s]+)", re.IGNORECASE),
        re.compile(r"(api[_-]?key=)([^&\s]+)", re.IGNORECASE),
        re.compile(r"(password=)([^&\s]+)", re.IGNORECASE),
        re.compile(r"(passwd=)([^&\s]+)", re.IGNORECASE),
        re.compile(r"(secret=)([^&\s]+)", re.IGNORECASE),
        re.compile(r"(access_key=)([^&\s]+)", re.IGNORECASE),
        re.compile(r"(username=)([^&\s]+)", re.IGNORECASE),
        re.compile(r"(telegram_id=)(\d+)", re.IGNORECASE),
        re.compile(r"(device(?:_id|_fingerprint)?=)([^&\s,}]+)", re.IGNORECASE),
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ]
    SSH_KEY_PATTERN = re.compile(
        r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----",
        re.MULTILINE,
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        for pattern in self.PATTERNS:
            replacement = "***REDACTED***" if pattern.groups == 0 else r"\1***REDACTED***"
            msg = pattern.sub(replacement, msg)
        msg = self.SSH_KEY_PATTERN.sub("***SSH_PRIVATE_KEY_REDACTED***", msg)
        record.msg = msg
        record.args = ()
        return True


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))
    root = logging.getLogger()
    privacy_filter = SecretMaskingFilter()
    root.addFilter(privacy_filter)
    for handler in root.handlers:
        handler.addFilter(privacy_filter)

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.disabled = True
    access_logger.propagate = False

    for noisy_logger in ("httpx", "httpcore", "paramiko", "paramiko.transport"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
