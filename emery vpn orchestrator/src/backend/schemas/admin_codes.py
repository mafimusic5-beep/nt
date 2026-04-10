from datetime import datetime

from pydantic import BaseModel


class AdminCodeItemResponse(BaseModel):
    id: int
    user_id: int
    telegram_id: int | None = None
    subscription_id: int
    code_hash: str
    status: str
    created_at: datetime
    first_redeemed_at: datetime | None = None
    subscription_status: str | None = None
    subscription_ends_at: datetime | None = None


class AdminCodeListResponse(BaseModel):
    items: list[AdminCodeItemResponse]
    total: int
    limit: int
    offset: int


class AdminCodeDeleteResponse(BaseModel):
    ok: bool = True
    deleted_id: int
