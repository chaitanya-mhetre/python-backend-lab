from datetime import UTC, datetime, timedelta

import pytest

from flowforge.domain.schedule import InvalidScheduleError, due_fire_time, validate_cron

T0 = datetime(2026, 1, 1, tzinfo=UTC)
LOOKBACK = timedelta(minutes=5)


@pytest.mark.parametrize("expr", ["*/5 * * * *", "0 9 * * 1-5", "  0   0 1 * * "])
def test_valid_expressions_are_normalised(expr: str) -> None:
    assert validate_cron(expr) == " ".join(expr.split())


@pytest.mark.parametrize("expr", ["", "* * * *", "0 0 0 * * *", "61 * * * *", "@reboot", "nope"])
def test_invalid_expressions_rejected(expr: str) -> None:
    with pytest.raises(InvalidScheduleError):
        validate_cron(expr)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC), datetime(2026, 1, 1, 12, 5, tzinfo=UTC)),
        (datetime(2026, 1, 1, 12, 5, 42, tzinfo=UTC), datetime(2026, 1, 1, 12, 5, tzinfo=UTC)),
        (datetime(2026, 1, 1, 12, 7, 0, tzinfo=UTC), datetime(2026, 1, 1, 12, 5, tzinfo=UTC)),
    ],
)
def test_latest_fire_time_at_or_before_now(now: datetime, expected: datetime) -> None:
    assert due_fire_time("*/5 * * * *", now=now, not_before=T0, lookback=LOOKBACK) == expected


def test_missed_runs_older_than_lookback_are_skipped() -> None:
    # daily at 09:00, scheduler checks at 09:30 with a 5-minute lookback: missed, don't fire late
    now = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
    assert due_fire_time("0 9 * * *", now=now, not_before=T0, lookback=LOOKBACK) is None


def test_outage_fires_only_the_latest_minute() -> None:
    now = datetime(2026, 1, 1, 13, 0, 10, tzinfo=UTC)  # after a long outage
    fire = due_fire_time("* * * * *", now=now, not_before=T0, lookback=LOOKBACK)
    assert fire == datetime(2026, 1, 1, 13, 0, tzinfo=UTC)


def test_never_fires_for_times_before_the_workflow_existed() -> None:
    now = datetime(2026, 1, 1, 12, 5, 30, tzinfo=UTC)
    created = datetime(2026, 1, 1, 12, 5, 10, tzinfo=UTC)
    assert due_fire_time("*/5 * * * *", now=now, not_before=created, lookback=LOOKBACK) is None
