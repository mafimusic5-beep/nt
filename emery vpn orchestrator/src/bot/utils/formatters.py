from datetime import datetime


PLAN_NAMES = {
    "warmup_1m": "Прогрев 1 месяц",
    "warmup_3m": "Прогрев 3 месяца",
    "warmup_6m": "Прогрев 6 месяцев",
    "warmup_12m": "Прогрев 12 месяцев",
    "warmup": "Прогрев",
}


def plan_name(plan_code: str | None) -> str:
    if not plan_code:
        return "Прогрев"
    return PLAN_NAMES.get(plan_code, plan_code)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def format_dt(value: str | None) -> str:
    dt = parse_dt(value)
    if not dt:
        return "—"
    return dt.strftime("%d.%m.%Y %H:%M")
