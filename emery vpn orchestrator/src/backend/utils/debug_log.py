"""Runtime analytics/evidence logging is intentionally disabled."""

from __future__ import annotations


def agent_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
    run_id: str = "run1",
) -> None:
    del hypothesis_id, location, message, data, run_id
    return None
