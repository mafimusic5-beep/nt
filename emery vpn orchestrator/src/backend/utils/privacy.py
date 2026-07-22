from __future__ import annotations

import re

DEFAULT_DEVICE_NAMES = {
    "android": "Android-устройство",
    "ios": "Мобильное устройство",
}

_TECHNICAL_DEVICE_NAME = re.compile(
    r"sdk[_ -]?gphone|google\s+sdk|android\s+sdk\s+built\s+for|"
    r"generic[_ -]?x86|x86_64|arm64-v8a|emulator|unknown\s+device",
    re.IGNORECASE,
)


def sanitize_device_name(value: object, platform: str = "android") -> str:
    """Return a short user-facing alias without exposing model or emulator metadata."""
    normalized = " ".join(str(value or "").replace("\x00", " ").split())[:64]
    generic = DEFAULT_DEVICE_NAMES.get((platform or "").strip().lower(), "Устройство")
    if not normalized or _TECHNICAL_DEVICE_NAME.search(normalized):
        return generic
    return normalized
