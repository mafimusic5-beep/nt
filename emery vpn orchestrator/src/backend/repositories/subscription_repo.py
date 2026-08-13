from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from src.backend.repositories.base import BaseRepository
from src.common.models import ActivationCode, Device, Subscription, User, VpnNode


class SubscriptionRepository(BaseRepository):
    def get_or_create_user(self, telegram_id: int) -> User:
        user = self.db.scalar(select(User).where(User.telegram_id == telegram_id))
        if user:
            return user
        user = User(telegram_id=telegram_id)
        self.db.add(user)
        self.db.flush()
        return user

    def get_active_subscription(self, user_id: int) -> Subscription | None:
        now = datetime.now(timezone.utc)
        return self.db.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.ends_at > now,
            )
        )

    def get_subscription(self, subscription_id: int) -> Subscription | None:
        return self.db.get(Subscription, subscription_id)

    def get_activation_code(self, code_hash: str) -> ActivationCode | None:
        return self.db.scalar(
            select(ActivationCode).where(ActivationCode.code_hash == code_hash, ActivationCode.status == "active")
        )

    def count_active_devices(self, subscription_id: int) -> int:
        return len(
            self.db.scalars(
                select(Device).where(
                    Device.subscription_id == subscription_id,
                    or_(Device.slot_index.is_not(None), Device.is_active.is_(True)),
                )
            ).all()
        )

    def find_device(self, subscription_id: int, fingerprint: str) -> Device | None:
        return self.db.scalar(
            select(Device).where(
                Device.subscription_id == subscription_id,
                Device.device_fingerprint == fingerprint,
            )
        )

    def upsert_device(
        self,
        subscription_id: int,
        fingerprint: str,
        platform: str,
        device_name: str,
        devices_limit: int,
    ) -> Device | None:
        now = datetime.now(timezone.utc)
        device = self.find_device(subscription_id, fingerprint)
        if device and device.slot_index is not None:
            device.is_active = True
            device.last_seen_at = now
            device.platform = platform
            device.device_name = device_name
            return device

        # A unique (subscription, slot_index) constraint is the final admission
        # gate. Concurrent workers may race on the same free slot, but only one
        # can commit it; losers try the next allowed slot and can never exceed
        # the immutable tariff limit.
        for slot_index in range(1, max(int(devices_limit), 0) + 1):
            candidate = device or Device(
                subscription_id=subscription_id,
                device_fingerprint=fingerprint,
                platform=platform,
                device_name=device_name,
                last_seen_at=now,
                is_active=True,
            )
            try:
                with self.db.begin_nested():
                    candidate.slot_index = slot_index
                    candidate.is_active = True
                    candidate.last_seen_at = now
                    candidate.platform = platform
                    candidate.device_name = device_name
                    self.db.add(candidate)
                    self.db.flush()
                return candidate
            except IntegrityError:
                self.db.expire_all()
                concurrent = self.find_device(subscription_id, fingerprint)
                if concurrent and concurrent.slot_index is not None:
                    concurrent.is_active = True
                    concurrent.last_seen_at = now
                    concurrent.platform = platform
                    concurrent.device_name = device_name
                    return concurrent
                device = concurrent
        return None

    def heartbeat(self, subscription_id: int, fingerprint: str) -> bool:
        device = self.find_device(subscription_id, fingerprint)
        if not device or not device.is_active:
            return False
        device.last_seen_at = datetime.now(timezone.utc)
        return True

    def unbind_device(self, subscription_id: int, fingerprint: str) -> bool:
        device = self.find_device(subscription_id, fingerprint)
        if not device or not device.is_active:
            return False
        device.is_active = False
        return True

    def get_active_node(self, region_code: str) -> VpnNode | None:
        return self.db.scalar(
            select(VpnNode).where(VpnNode.region_code == region_code, VpnNode.status == "active").order_by(VpnNode.id.asc())
        )

    def list_devices(self, subscription_id: int) -> list[Device]:
        return self.db.scalars(
            select(Device).where(Device.subscription_id == subscription_id, Device.is_active.is_(True)).order_by(Device.created_at.asc())
        ).all()

    def list_codes_by_user(self, user_id: int) -> list[ActivationCode]:
        return self.db.scalars(
            select(ActivationCode).where(ActivationCode.user_id == user_id).order_by(ActivationCode.created_at.desc())
        ).all()
