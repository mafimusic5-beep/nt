import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.models import Plan

logger = logging.getLogger(__name__)


def seed_plans(db: Session) -> None:
    existing = {p.code: p for p in db.scalars(select(Plan)).all()}
    plans = [
        ("personal_1m", "Личный", 1, 200, 1),
        ("personal_plus_1m", "Личный+", 1, 260, 2),
        ("family_1m", "Семейный", 1, 500, 5),
        # Legacy products stay readable so old orders remain valid.
        ("warmup_1m", "Прогрев 1 месяц", 1, 600, 5),
        ("warmup_3m", "Прогрев 3 месяца", 3, 1500, 5),
        ("warmup_6m", "Прогрев 6 месяцев", 6, 2700, 5),
        ("warmup_12m", "Прогрев 12 месяцев", 12, 4800, 5),
    ]
    logger.info("seed_plans: existing=%d target=%d", len(existing), len(plans))
    for code, name, months, rub, devices_limit in plans:
        current = existing.get(code)
        if current:
            # The device limit is an access-control rule, so repair stale rows.
            current.devices_limit = devices_limit
            continue
        db.add(
            Plan(
                code=code,
                name=name,
                duration_months=months,
                price_rub=rub,
                devices_limit=devices_limit,
                is_active=True,
            )
        )
    db.commit()
