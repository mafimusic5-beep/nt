import pytest
from fastapi import HTTPException

from src.backend.services.subscription_service import SubscriptionService


def test_user_device_unbind_is_permanently_disabled(db_session):
    with pytest.raises(HTTPException) as exc:
        SubscriptionService(db_session).unbind(
            telegram_id=1001,
            fingerprint="registered-device",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "device_unbind_disabled"
