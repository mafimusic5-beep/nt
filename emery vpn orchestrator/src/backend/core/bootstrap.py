import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.models import Plan

logger = logging.getLogger(__name__)


def seed_plans(db: Session) -> None:
    existing = {p.code for p in db.scalars(select(Plan)).all()}
    plans = [
        ("warmup_1m", "Прогрев 1 месяц", 1, 600),
        ("warmup_3m", "Прогрев 3 месяца", 3, 1500),
        ("warmup_6m", "Прогрев 6 месяцев", 6, 2700),
        ("warmup_12m", "Прогрев 12 месяцев", 12, 4800),
    ]
    logger.info("seed_plans: existing=%d target=%d", len(existing), len(plans))
    for code, name, months, rub in plans:
        if code in existing:
            continue
        db.add(Plan(code=code, name=name, duration_months=months, price_rub=rub, is_active=True))
    db.commit()
