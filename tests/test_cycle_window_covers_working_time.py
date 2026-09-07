"""Structural guard: the run cycle covers every hour devclaw is allowed to work.

The cycle window is the denominator of the self-improving loop. `self_issue.py`
files a GitHub issue once a problem survives `SELF_ISSUE_MIN_CYCLES` distinct
cycles, and a problem enters a cycle only if its last occurrence falls inside
that window (`problems_active_in_window`).

The window used to be a hardcoded 22:00–05:00 night shift. When 24/7 operation
was ruled (2026-09-05) and the run window disabled, the filer kept slicing the
day as a night: ~70% of devclaw's problems entered no cycle, the recurrence
count never advanced, and the filer went quiet — 1 issue filed against 40
catalogued problems, with the owner triaging by hand every morning.

Nothing failed. That is why this guard is structural rather than behavioural:
the defect is a window that silently stops describing reality, and only an
assertion about coverage can see it (specs/tiny/cycle-is-when-devclaw-works).
"""
from __future__ import annotations

from datetime import datetime, timezone

from devclaw.goal.cycle_report import cycle_window_for, most_recent_closed_window

_TZ = "Europe/Dublin"


def _ms(y, mo, d, h, mi=0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


def test_a_disabled_run_window_makes_the_cycle_the_whole_day() -> None:
    """24/7 operation ⇒ the cycle is the calendar day, not a night shift."""
    start, end, tz = cycle_window_for({"enabled": False, "start": "22:00",
                                       "end": "05:00", "tz": _TZ})
    assert (start, end) == ("00:00", "00:00")
    assert tz == _TZ, "the tz still says where the operator's day boundary falls"

    key, s_ms, e_ms = most_recent_closed_window(
        _ms(2026, 9, 7, 12), start=start, end=end, tz=tz
    )
    assert e_ms - s_ms == 24 * 3600 * 1000, "a disabled window must cover a full day"
    assert key == "2026-09-06"


def test_an_enabled_run_window_is_the_cycle_verbatim() -> None:
    """A configured window still means exactly itself — the fix must not
    silently widen an operator's deliberate night shift."""
    start, end, tz = cycle_window_for({"enabled": True, "start": "22:00",
                                       "end": "05:00", "tz": _TZ})
    assert (start, end, tz) == ("22:00", "05:00", _TZ)
    _key, s_ms, e_ms = most_recent_closed_window(
        _ms(2026, 9, 7, 12), start=start, end=end, tz=tz
    )
    assert e_ms - s_ms == 7 * 3600 * 1000


def test_every_hour_of_a_24_7_day_lands_inside_some_cycle() -> None:
    """The regression, stated as coverage.

    Under 24/7 operation a problem occurring at ANY hour must fall inside the
    cycle that closes after it — that membership is what advances
    `problem_cycle_count` and eventually files the issue. Under the old
    hardcoded night window the afternoon hours belonged to no cycle at all, so
    a problem recurring every afternoon could never be filed.
    """
    start, end, tz = cycle_window_for({"enabled": False, "tz": _TZ})
    uncovered = []
    for hour in range(24):
        seen_at = _ms(2026, 9, 6, hour)
        # the cycle that closes AFTER the occurrence: probe a day later so the
        # window containing `seen_at` has certainly closed
        _key, s_ms, e_ms = most_recent_closed_window(
            seen_at + 24 * 3600 * 1000, start=start, end=end, tz=tz
        )
        if not (s_ms <= seen_at <= e_ms):
            uncovered.append(hour)
    assert not uncovered, (
        f"hours belonging to no run cycle: {uncovered} — a problem seen then "
        "never advances its recurrence count, so the self-issue filer can "
        "never file it"
    )


def test_the_old_night_window_is_what_lost_the_afternoon() -> None:
    """Pins the diagnosis, so a future 'let's just hardcode a window again'
    has to argue with the arithmetic rather than rediscover it."""
    uncovered = []
    for hour in range(24):
        seen_at = _ms(2026, 9, 6, hour)
        _key, s_ms, e_ms = most_recent_closed_window(
            seen_at + 24 * 3600 * 1000, start="22:00", end="05:00", tz="Europe/London"
        )
        if not (s_ms <= seen_at <= e_ms):
            uncovered.append(hour)
    assert len(uncovered) > 12, (
        "the historic 22:00-05:00 cycle left most of the day uncounted; if this "
        "no longer holds the diagnosis in the tinyspec needs revisiting"
    )


def test_an_unresolvable_timezone_still_fails_safe() -> None:
    """Unchanged contract: a bad tz skips the report, never crashes the
    heartbeat."""
    assert most_recent_closed_window(_ms(2026, 9, 7, 12), tz="Not/AZone") is None
