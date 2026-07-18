from __future__ import annotations

from src.common.config import settings


APP_VERSION_HEADER = "X-Skryon-App-Version-Code"


def app_update_required(app_version_code: int | None) -> bool:
    minimum = max(settings.min_supported_app_version_code, 0)
    if minimum == 0:
        return False
    return bool(app_version_code and app_version_code < minimum)


def app_update_metadata() -> dict[str, object]:
    return {
        "update_required": True,
        "min_version_code": settings.min_supported_app_version_code,
        "message": settings.app_update_message,
    }


def app_update_server_placeholder() -> dict[str, object]:
    return {
        "id": 0,
        "city": settings.app_update_message,
        "health_status": "upgrade_required",
        "is_available": True,
    }
