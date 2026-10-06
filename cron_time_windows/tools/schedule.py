"""Pure scheduling engine of ``cron_time_windows``.

Nothing in this module touches the ORM or the database: the functions take plain values
and return plain values, so the whole scheduling logic can be tested on its own.

Conventions
-----------
* Every datetime is *naive*. Names ending in ``_utc``, and the bounds of a :class:`Span`,
  are UTC instants; names ending in ``_local`` are wall-clock times in the schedule
  timezone.
* A time of day is an integer number of minutes after midnight, ``0 <= minutes < 1440``.
* Weekdays follow :meth:`datetime.date.weekday`: Monday is 0, Sunday is 6.
* A *step* is the spacing between two executions: a :class:`~datetime.timedelta` for
  fixed-length units, a :class:`~dateutil.relativedelta.relativedelta` for months.

Daylight saving time
--------------------
Wall-clock times are converted by :func:`local_to_utc`. A time that happens twice
(clocks set back) resolves to its first occurrence, and a time that never happens (clocks
set forward) is pushed forward by the length of the gap: 02:30 becomes 03:30. Window
boundaries follow the wall clock, while the slots inside a window are spaced in real
elapsed time, so "every 5 minutes" keeps meaning 300 seconds across a transition.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Union

import pytz
from dateutil.relativedelta import relativedelta

MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY
ALL_WEEKDAYS: frozenset[int] = frozenset(range(7))

#: Local days generated on each side of the reference instant when building spans.
#: Window patterns repeat every week, so eight days always contain the span that started
#: before the instant and the next running span after it.
WINDOW_HORIZON_DAYS = 8

#: Days scanned to find an occurrence of a fixed time: a monthly line fires at least once
#: every 31 days, whatever the length of the months.
FIXED_HORIZON_DAYS = 64

Step = Union[timedelta, relativedelta]


# ---------------------------------------------------------------------------
# Timezone conversions
# ---------------------------------------------------------------------------

def utc_to_local(tz: pytz.BaseTzInfo, value_utc: datetime) -> datetime:
    """Return the wall-clock time in ``tz`` of the naive UTC instant ``value_utc``."""
    return pytz.utc.localize(value_utc).astimezone(tz).replace(tzinfo=None)


def local_to_utc(tz: pytz.BaseTzInfo, value_local: datetime) -> datetime:
    """Return the naive UTC instant of the wall-clock time ``value_local`` in ``tz``.

    The candidates are built from every UTC offset in force within a day of the given
    time. An ambiguous time has two valid candidates and the earlier one wins; a skipped
    time has none, and the later candidate, which keeps the wall-clock distance to the
    jump, is returned.
    """
    offsets = {
        pytz.utc.localize(value_local + shift).astimezone(tz).utcoffset()
        for shift in (timedelta(days=-1), timedelta(0), timedelta(days=1))
    }
    candidates = sorted(value_local - offset for offset in offsets)
    for candidate in candidates:
        if utc_to_local(tz, candidate) == value_local:
            return candidate
    return candidates[-1]


# ---------------------------------------------------------------------------
# Concrete spans
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Span:
    """A concrete span of time in UTC, ``start <= t < end``.

    Executions happen at ``start``, ``start + step``, ``start + 2 * step``... as long as
    they fall before ``end``. A ``step`` of ``None`` marks a span during which the job
    must not run.
    """

    start: datetime
    end: datetime
    step: Step | None

    def contains(self, instant_utc: datetime) -> bool:
        """Tell whether ``instant_utc`` lies within the span."""
        return self.start <= instant_utc < self.end

    def slot_at(self, instant_utc: datetime) -> datetime:
        """Return the latest slot at or before ``instant_utc``, which must be in the span."""
        if isinstance(self.step, timedelta):
            return self.start + (instant_utc - self.start) // self.step * self.step
        count = 0
        while self.start + self.step * (count + 1) <= instant_utc:
            count += 1
        return self.start + self.step * count

    def first_slot_after(self, instant_utc: datetime) -> datetime | None:
        """Return the first slot strictly after ``instant_utc``, or ``None`` past the end."""
        if self.step is None:
            return None
        if instant_utc < self.start:
            slot = self.start
        elif isinstance(self.step, timedelta):
            slot = self.slot_at(instant_utc) + self.step
        else:
            count = 1
            while self.start + self.step * count <= instant_utc:
                count += 1
            slot = self.start + self.step * count
        return slot if slot < self.end else None


# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DailyWindow:
    """A daily time window as configured: wall-clock bounds, a step and weekdays.

    ``end <= start`` means that the window ends on the next day, and ``end == start``
    spans 24 hours. A window that crosses midnight belongs to the day it starts on: a
    Friday 22:00-06:00 window covers Friday night up to Saturday 06:00. A ``step`` of
    ``None`` makes a pausing window.
    """

    start: int
    end: int
    step: Step | None
    weekdays: frozenset[int] = ALL_WEEKDAYS

    @property
    def duration(self) -> int:
        """Length of the window in minutes, between 1 and 1440."""
        return (self.end - self.start) % MINUTES_PER_DAY or MINUTES_PER_DAY


def spans_around(
    windows: Sequence[DailyWindow],
    tz: pytz.BaseTzInfo,
    around_utc: datetime,
    outside_step: Step | None = None,
) -> list[Span]:
    """Return the concrete spans of ``windows`` around ``around_utc``, sorted by start.

    The result covers at least a week on each side of the instant. When ``outside_step``
    is given, every gap between two windows becomes a span of its own, running at that
    step from the end of the previous window.
    """
    first_day = utc_to_local(tz, around_utc).date() - timedelta(days=WINDOW_HORIZON_DAYS)
    spans = []
    for offset in range(2 * WINDOW_HORIZON_DAYS + 1):
        day = first_day + timedelta(days=offset)
        midnight = datetime.combine(day, time.min)
        for window in windows:
            if day.weekday() not in window.weekdays:
                continue
            start_local = midnight + timedelta(minutes=window.start)
            start = local_to_utc(tz, start_local)
            end = local_to_utc(tz, start_local + timedelta(minutes=window.duration))
            if end > start:
                spans.append(Span(start, end, window.step))
    spans.sort(key=lambda span: span.start)
    if outside_step is None:
        return spans
    return _fill_gaps(spans, outside_step)


def _fill_gaps(spans: list[Span], step: Step) -> list[Span]:
    """Insert a span running at ``step`` in every gap between sorted ``spans``."""
    filled = []
    reached = None
    for span in spans:
        if reached is not None and span.start > reached:
            filled.append(Span(reached, span.start, step))
        filled.append(span)
        reached = span.end if reached is None else max(reached, span.end)
    return filled


def next_window_run(
    windows: Sequence[DailyWindow],
    tz: pytz.BaseTzInfo,
    after_utc: datetime,
    outside_step: Step | None = None,
) -> datetime | None:
    """Return the first execution strictly after ``after_utc``, or ``None`` if none exists.

    Inside a running window, that is the next slot of the window; past its end, or inside
    a pausing window, the first slot of the next running span. Outside every window, the
    gaps run at ``outside_step`` when it is given and are skipped otherwise.
    """
    for span in spans_around(windows, tz, after_utc, outside_step):
        if span.step is None or span.end <= after_utc:
            continue
        slot = span.first_slot_after(after_utc)
        if slot is not None:
            return slot
    return None


def current_window_slot(
    windows: Sequence[DailyWindow],
    tz: pytz.BaseTzInfo,
    at_utc: datetime,
    outside_step: Step | None = None,
) -> datetime | None:
    """Return the slot that ``at_utc`` belongs to, or ``None`` if the job may not run then."""
    for span in spans_around(windows, tz, at_utc, outside_step):
        if span.contains(at_utc):
            return span.slot_at(at_utc) if span.step is not None else None
    return None


def _week_segments(window: DailyWindow) -> list[tuple[int, int]]:
    """Return the minutes of the week covered by ``window``, wrapping Sunday into Monday."""
    segments = []
    for weekday in sorted(window.weekdays):
        start = weekday * MINUTES_PER_DAY + window.start
        end = start + window.duration
        if end <= MINUTES_PER_WEEK:
            segments.append((start, end))
        else:
            segments.append((start, MINUTES_PER_WEEK))
            segments.append((0, end - MINUTES_PER_WEEK))
    return segments


def find_overlaps(windows: Sequence[DailyWindow]) -> list[tuple[int, int, int]]:
    """Return ``(first, second, weekday)`` for every pair of overlapping windows.

    ``first`` and ``second`` are indexes in ``windows`` and ``weekday`` is the day on
    which the overlap starts. A window that crosses midnight is checked against the
    windows of the next day too.
    """
    segments = [
        (start, end, index)
        for index, window in enumerate(windows)
        for start, end in _week_segments(window)
    ]
    found: dict[tuple[int, int], int] = {}
    for position, (start_a, end_a, index_a) in enumerate(segments):
        for start_b, end_b, index_b in segments[position + 1:]:
            if index_a == index_b or not (start_a < end_b and start_b < end_a):
                continue
            key = (min(index_a, index_b), max(index_a, index_b))
            found.setdefault(key, max(start_a, start_b) // MINUTES_PER_DAY % 7)
    return [(first, second, weekday) for (first, second), weekday in sorted(found.items())]


# ---------------------------------------------------------------------------
# Fixed times
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FixedTime:
    """A fixed execution time: every day, one weekday a week, or one day a month.

    A monthly time set on a day the month lacks (the 31st in April) fires on the last
    day of that month instead.
    """

    frequency: str
    minute: int
    weekday: int | None = None
    day_of_month: int | None = None

    def fires_on(self, day: date) -> bool:
        """Tell whether the time applies to the local date ``day``."""
        if self.frequency == 'daily':
            return True
        if self.frequency == 'weekly':
            return day.weekday() == self.weekday
        if self.frequency == 'monthly' and self.day_of_month:
            last_day = calendar.monthrange(day.year, day.month)[1]
            return day.day == min(self.day_of_month, last_day)
        return False

    def occurrence(self, tz: pytz.BaseTzInfo, day: date) -> datetime | None:
        """Return the UTC instant of the time on the local date ``day``, if it applies."""
        if not self.fires_on(day):
            return None
        return local_to_utc(tz, datetime.combine(day, time.min) + timedelta(minutes=self.minute))


def next_fixed_run(
    fixed_times: Sequence[FixedTime],
    tz: pytz.BaseTzInfo,
    after_utc: datetime,
) -> datetime | None:
    """Return the earliest occurrence strictly after ``after_utc`` across ``fixed_times``."""
    first_day = utc_to_local(tz, after_utc).date() - timedelta(days=1)
    best = None
    for fixed_time in fixed_times:
        for offset in range(FIXED_HORIZON_DAYS):
            occurrence = fixed_time.occurrence(tz, first_day + timedelta(days=offset))
            if occurrence is not None and occurrence > after_utc:
                best = occurrence if best is None else min(best, occurrence)
                break
    return best


def current_fixed_slot(
    fixed_times: Sequence[FixedTime],
    tz: pytz.BaseTzInfo,
    at_utc: datetime,
) -> datetime | None:
    """Return the latest occurrence at or before ``at_utc`` across ``fixed_times``."""
    last_day = utc_to_local(tz, at_utc).date() + timedelta(days=1)
    best = None
    for fixed_time in fixed_times:
        for offset in range(FIXED_HORIZON_DAYS):
            occurrence = fixed_time.occurrence(tz, last_day - timedelta(days=offset))
            if occurrence is not None and occurrence <= at_utc:
                best = occurrence if best is None else max(best, occurrence)
                break
    return best
