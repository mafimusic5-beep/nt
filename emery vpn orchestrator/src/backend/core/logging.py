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
    root.addFilter(SecretMaskingFilter())

    for noisy_logger in ("httpx", "httpcore", "paramiko", "paramiko.transport"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
