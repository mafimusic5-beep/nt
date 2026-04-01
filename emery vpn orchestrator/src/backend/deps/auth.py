from fastapi import Header, HTTPException

from src.common.config import settings


def require_internal_api_key(x_internal_api_key: str = Header(default="")) -> None:
    if not settings.internal_api_key or x_internal_api_key != settings.internal_api_key:
        raise HTTPException(status_code=403, detail="forbidden")


def require_admin_api_key(x_admin_api_key: str = Header(default="")) -> None:
    if not settings.admin_api_key or x_admin_api_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="forbidden")
