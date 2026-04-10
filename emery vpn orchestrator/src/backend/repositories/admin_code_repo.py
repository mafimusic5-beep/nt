from sqlalchemy import String, cast, func, or_, select

from src.backend.repositories.base import BaseRepository
from src.common.models import ActivationCode, Subscription, User


class AdminCodeRepository(BaseRepository):
    def _stmt(self):
        return (
            select(
                ActivationCode,
                User.telegram_id.label("telegram_id"),
                Subscription.status.label("subscription_status"),
                Subscription.ends_at.label("subscription_ends_at"),
            )
            .join(User, User.id == ActivationCode.user_id)
            .join(Subscription, Subscription.id == ActivationCode.subscription_id, isouter=True)
        )

    @staticmethod
    def _filter(query: str):
        term = f"%{query.strip().lower()}%"
        return or_(
            cast(ActivationCode.id, String).like(term),
            cast(ActivationCode.user_id, String).like(term),
            cast(ActivationCode.subscription_id, String).like(term),
            cast(User.telegram_id, String).like(term),
            func.lower(ActivationCode.status).like(term),
            func.lower(ActivationCode.code_hash).like(term),
        )

    def list(self, limit: int, offset: int):
        return self.db.execute(
            self._stmt().order_by(ActivationCode.created_at.desc(), ActivationCode.id.desc()).limit(limit).offset(offset)
        ).all()

    def count(self) -> int:
        return int(self.db.scalar(select(func.count(ActivationCode.id))) or 0)

    def search(self, query: str, limit: int, offset: int):
        return self.db.execute(
            self._stmt()
            .where(self._filter(query))
            .order_by(ActivationCode.created_at.desc(), ActivationCode.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()

    def count_search(self, query: str) -> int:
        return int(
            self.db.scalar(
                select(func.count(ActivationCode.id))
                .select_from(ActivationCode)
                .join(User, User.id == ActivationCode.user_id)
                .where(self._filter(query))
            )
            or 0
        )

    def get(self, code_id: int):
        return self.db.execute(self._stmt().where(ActivationCode.id == code_id)).first()

    def get_model(self, code_id: int) -> ActivationCode | None:
        return self.db.get(ActivationCode, code_id)

    def set_status(self, code_id: int, status: str) -> ActivationCode | None:
        code = self.get_model(code_id)
        if not code:
            return None
        code.status = status
        self.db.flush()
        return code

    def delete(self, code_id: int) -> ActivationCode | None:
        code = self.get_model(code_id)
        if not code:
            return None
        self.db.delete(code)
        self.db.flush()
        return code
