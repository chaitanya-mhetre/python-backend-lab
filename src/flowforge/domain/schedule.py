"""Cron schedules (pure logic, no I/O).

Cron expressions are evaluated in **UTC**. The scheduler asks one question every tick:
"which fire time, if any, is due for this workflow right now?"
"""

from __future__ import annotations

from datetime import datetime, timedelta

from croniter import croniter

from flowforge.domain.errors import DomainError


class InvalidScheduleError(DomainError):
    code = "invalid_schedule"


def validate_cron(expr: str) -> str:
    """Accept standard 5-field cron only (no seconds field, no ``@reboot``)."""
    expr = " ".join(expr.split())
    if len(expr.split(" ")) != 5 or not croniter.is_valid(expr):
        raise InvalidScheduleError(f"invalid cron expression: {expr!r}")
    return expr


def due_fire_time(
    expr: str, *, now: datetime, not_before: datetime, lookback: timedelta
) -> datetime | None:
    """The most recent scheduled time ``<= now``, if it should still be fired.

    Misfire policy: *fire once, then catch up to the present*. If the scheduler was down for
    an hour on a ``* * * * *`` schedule we fire the latest minute only, not 60 stale runs.
    A time older than ``lookback`` is considered missed and skipped, and a workflow never
    fires for a time before it existed (``not_before``).
    """
    # Cron has minute resolution. Starting from the next minute boundary and stepping back
    # once gives the latest fire time <= now (get_prev is strictly earlier than its start).
    next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    latest: datetime = croniter(expr, next_minute).get_prev(datetime)
    if latest < not_before or now - latest > lookback:
        return None
    return latest
