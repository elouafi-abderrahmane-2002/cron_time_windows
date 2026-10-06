from datetime import datetime, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo.tests import tagged

from odoo.addons.cron_time_windows.tools.schedule import (
    DailyWindow,
    FixedTime,
    current_fixed_slot,
    current_window_slot,
    find_overlaps,
    local_to_utc,
    next_fixed_run,
    next_window_run,
)

from .common import CASABLANCA, CronTwCase


def minutes(hhmm):
    """Return the minutes after midnight of ``HH:MM``."""
    hours, mins = hhmm.split(':')
    return int(hours) * 60 + int(mins)


EVERY_5_MIN = timedelta(minutes=5)
EVERY_30_MIN = timedelta(minutes=30)
EVERY_HOUR = timedelta(hours=1)

SYNC_ADS = [
    DailyWindow(minutes('08:00'), minutes('18:00'), EVERY_5_MIN),
    DailyWindow(minutes('18:00'), minutes('23:00'), EVERY_30_MIN),
    DailyWindow(minutes('23:00'), minutes('08:00'), None),
]


@tagged('post_install', '-at_install')
class TestScheduleEngine(CronTwCase):
    """The pure engine: window selection, midnight, pauses, fallback, fixed times, DST."""

    def assertNextWindow(self, windows, after, expected, outside_step=None):
        result = next_window_run(windows, CASABLANCA, self.utc(after), outside_step)
        self.assertEqual(self.local(result) if result else None, expected, "after %s" % after)

    def assertWindowSlot(self, windows, at, expected, outside_step=None):
        result = current_window_slot(windows, CASABLANCA, self.utc(at), outside_step)
        self.assertEqual(self.local(result) if result else None, expected, "at %s" % at)

    def assertNextFixed(self, fixed_times, after, expected):
        result = next_fixed_run(fixed_times, CASABLANCA, self.utc(after))
        self.assertEqual(self.local(result) if result else None, expected, "after %s" % after)

    def test_sync_ads_window_selection(self):
        for after, expected in [
            ('2026-09-28 10:02', '2026-09-28 10:05'),
            ('2026-09-28 10:05', '2026-09-28 10:10'),
            ('2026-09-28 17:57', '2026-09-28 18:00'),
            ('2026-09-28 18:10', '2026-09-28 18:30'),
            ('2026-09-28 22:30', '2026-09-29 08:00'),
            ('2026-09-28 23:30', '2026-09-29 08:00'),
            ('2026-09-29 03:00', '2026-09-29 08:00'),
            ('2026-09-29 07:59', '2026-09-29 08:00'),
        ]:
            self.assertNextWindow(SYNC_ADS, after, expected)

    def test_slots_are_aligned_on_window_start(self):
        windows = [DailyWindow(minutes('08:00'), minutes('18:00'), timedelta(minutes=7))]
        self.assertNextWindow(windows, '2026-09-28 08:00', '2026-09-28 08:07')
        self.assertNextWindow(windows, '2026-09-28 08:03', '2026-09-28 08:07')
        self.assertNextWindow(windows, '2026-09-28 17:50', '2026-09-28 17:55')
        # 18:02 would be past the end of the window.
        self.assertNextWindow(windows, '2026-09-28 17:55', '2026-09-29 08:00')

    def test_current_slot(self):
        self.assertWindowSlot(SYNC_ADS, '2026-09-28 10:07', '2026-09-28 10:05')
        self.assertWindowSlot(SYNC_ADS, '2026-09-28 18:45', '2026-09-28 18:30')
        self.assertWindowSlot(SYNC_ADS, '2026-09-28 23:30', None)
        self.assertWindowSlot(SYNC_ADS, '2026-09-29 07:59', None)

    def test_midnight_crossing(self):
        windows = [DailyWindow(minutes('22:00'), minutes('06:00'), EVERY_HOUR)]
        self.assertNextWindow(windows, '2026-09-28 21:00', '2026-09-28 22:00')
        self.assertNextWindow(windows, '2026-09-28 23:30', '2026-09-29 00:00')
        self.assertNextWindow(windows, '2026-09-29 04:10', '2026-09-29 05:00')
        self.assertNextWindow(windows, '2026-09-29 05:30', '2026-09-29 22:00')
        self.assertWindowSlot(windows, '2026-09-29 02:59', '2026-09-29 02:00')
        self.assertWindowSlot(windows, '2026-09-29 12:00', None)

    def test_midnight_window_belongs_to_its_start_day(self):
        # Friday 2026-10-02 22:00 to Saturday 02:00, every 30 minutes.
        windows = [DailyWindow(minutes('22:00'), minutes('02:00'), EVERY_30_MIN, frozenset({4}))]
        self.assertNextWindow(windows, '2026-10-03 01:10', '2026-10-03 01:30')
        self.assertNextWindow(windows, '2026-10-03 01:45', '2026-10-09 22:00')
        self.assertWindowSlot(windows, '2026-10-03 01:10', '2026-10-03 01:00')
        self.assertWindowSlot(windows, '2026-10-03 23:00', None)
        self.assertWindowSlot(windows, '2026-10-01 23:00', None)

    def test_pause_window_is_not_outside(self):
        windows = [
            DailyWindow(minutes('08:00'), minutes('12:00'), EVERY_HOUR),
            DailyWindow(minutes('12:00'), minutes('14:00'), None),
            DailyWindow(minutes('14:00'), minutes('18:00'), EVERY_HOUR),
        ]
        # Even with a fallback interval, a pausing window never runs.
        fallback = timedelta(minutes=15)
        self.assertNextWindow(windows, '2026-09-28 11:30', '2026-09-28 14:00', fallback)
        self.assertNextWindow(windows, '2026-09-28 12:10', '2026-09-28 14:00', fallback)
        self.assertWindowSlot(windows, '2026-09-28 13:00', None, fallback)
        # After 18:00 no window applies: the fallback interval runs from the window end.
        self.assertNextWindow(windows, '2026-09-28 18:00', '2026-09-28 18:15', fallback)

    def test_outside_windows_fallback(self):
        windows = [DailyWindow(minutes('08:00'), minutes('12:00'), timedelta(minutes=15))]
        self.assertNextWindow(windows, '2026-09-28 11:50', '2026-09-28 12:00', EVERY_HOUR)
        self.assertNextWindow(windows, '2026-09-28 12:00', '2026-09-28 13:00', EVERY_HOUR)
        self.assertNextWindow(windows, '2026-09-28 13:10', '2026-09-28 14:00', EVERY_HOUR)
        # The last fallback slot is 07:00; the window start wins at 08:00.
        self.assertNextWindow(windows, '2026-09-29 07:30', '2026-09-29 08:00', EVERY_HOUR)
        self.assertWindowSlot(windows, '2026-09-28 13:10', '2026-09-28 13:00', EVERY_HOUR)
        # Calendar steps: a monthly fallback runs once, when the gap opens.
        monthly = relativedelta(months=1)
        self.assertNextWindow(windows, '2026-09-28 11:50', '2026-09-28 12:00', monthly)
        self.assertNextWindow(windows, '2026-09-28 13:10', '2026-09-29 08:00', monthly)
        # Without a fallback, nothing runs outside the window.
        self.assertNextWindow(windows, '2026-09-28 13:10', '2026-09-29 08:00')
        self.assertWindowSlot(windows, '2026-09-28 13:10', None)

    def test_weekdays(self):
        office_days = frozenset(range(5))
        windows = [DailyWindow(minutes('08:00'), minutes('18:00'), EVERY_5_MIN, office_days)]
        self.assertNextWindow(windows, '2026-10-02 17:58', '2026-10-05 08:00')
        self.assertWindowSlot(windows, '2026-10-03 10:00', None)
        self.assertWindowSlot(windows, '2026-10-05 10:01', '2026-10-05 10:00')

    def test_daily_report_fixed_times(self):
        fixed_times = [
            FixedTime('daily', minutes('07:30')),
            FixedTime('weekly', minutes('09:00'), weekday=0),
        ]
        self.assertNextFixed(fixed_times, '2026-09-27 08:00', '2026-09-28 07:30')
        self.assertNextFixed(fixed_times, '2026-09-28 07:30', '2026-09-28 09:00')
        self.assertNextFixed(fixed_times, '2026-09-28 09:00', '2026-09-29 07:30')
        self.assertNextFixed(fixed_times, '2026-09-29 07:30', '2026-09-30 07:30')
        for at, expected in [
            ('2026-09-28 08:00', '2026-09-28 07:30'),
            ('2026-09-28 09:10', '2026-09-28 09:00'),
            ('2026-09-29 07:00', '2026-09-28 09:00'),
        ]:
            slot = current_fixed_slot(fixed_times, CASABLANCA, self.utc(at))
            self.assertEqual(self.local(slot), expected, "at %s" % at)

    def test_monthly_short_months(self):
        end_of_month = [FixedTime('monthly', minutes('09:00'), day_of_month=31)]
        self.assertNextFixed(end_of_month, '2027-01-31 09:00', '2027-02-28 09:00')
        self.assertNextFixed(end_of_month, '2027-03-01 00:00', '2027-03-31 09:00')
        self.assertNextFixed(end_of_month, '2027-04-01 00:00', '2027-04-30 09:00')
        self.assertNextFixed(end_of_month, '2028-02-01 00:00', '2028-02-29 09:00')
        mid_month = [FixedTime('monthly', minutes('09:00'), day_of_month=15)]
        self.assertNextFixed(mid_month, '2027-02-15 09:00', '2027-03-15 09:00')

    def test_find_overlaps(self):
        self.assertEqual(find_overlaps(SYNC_ADS), [], "adjacent windows do not overlap")
        self.assertEqual(
            find_overlaps([DailyWindow(minutes('08:00'), minutes('18:00'), EVERY_5_MIN),
                           DailyWindow(minutes('17:00'), minutes('20:00'), EVERY_5_MIN)]),
            [(0, 1, 0)],
        )
        self.assertEqual(
            find_overlaps([DailyWindow(minutes('08:00'), minutes('18:00'), EVERY_5_MIN, frozenset({0})),
                           DailyWindow(minutes('09:00'), minutes('10:00'), EVERY_5_MIN, frozenset({1}))]),
            [],
            "windows on different weekdays do not overlap",
        )
        # A Friday night window runs into Saturday morning.
        self.assertEqual(
            find_overlaps([DailyWindow(minutes('22:00'), minutes('06:00'), EVERY_HOUR, frozenset({4})),
                           DailyWindow(minutes('05:00'), minutes('09:00'), EVERY_HOUR, frozenset({5}))]),
            [(0, 1, 5)],
        )
        # A Sunday night window runs into Monday morning.
        self.assertEqual(
            find_overlaps([DailyWindow(minutes('22:00'), minutes('06:00'), EVERY_HOUR, frozenset({6})),
                           DailyWindow(minutes('05:00'), minutes('09:00'), EVERY_HOUR, frozenset({0}))]),
            [(0, 1, 0)],
        )

    # ------------------------------------------------------------------
    # Daylight saving time: Morocco leaves UTC+1 for UTC+0 during Ramadan
    # ------------------------------------------------------------------

    def _ramadan_transitions(self):
        """Return the clocks-back and clocks-forward transitions of one Ramadan.

        Each transition is ``(utc_instant, offset_before, offset_after)``. They are read
        from the installed pytz rather than hardcoded, since the dates follow the lunar
        calendar and the tz database.
        """
        def offset(moment):
            return pytz.utc.localize(moment).astimezone(CASABLANCA).utcoffset()

        for year in (2026, 2027, 2025, 2028):
            back = forward = None
            moment = datetime(year, 1, 1)
            previous = offset(moment)
            while moment.year == year and forward is None:
                moment += EVERY_HOUR
                current = offset(moment)
                if current < previous and back is None:
                    back = (moment, previous, current)
                elif current > previous and back is not None:
                    forward = (moment, previous, current)
                previous = current
            if back and forward:
                return back, forward
        self.skipTest("The installed pytz knows no Ramadan transition for Africa/Casablanca.")

    def _window_slots(self, windows, start_utc, end_utc):
        """Return the executions of ``windows`` within ``[start_utc, end_utc)``."""
        slots = []
        moment = next_window_run(windows, CASABLANCA, start_utc - timedelta(minutes=1))
        while moment is not None and moment < end_utc:
            slots.append(moment)
            moment = next_window_run(windows, CASABLANCA, moment)
        return slots

    def _night_window_check(self, day, expected_hours):
        night = [DailyWindow(minutes('00:00'), minutes('06:00'), EVERY_HOUR)]
        midnight = datetime.combine(day, datetime.min.time())
        start = local_to_utc(CASABLANCA, midnight)
        end = local_to_utc(CASABLANCA, midnight + timedelta(hours=6))
        self.assertEqual(end - start, timedelta(hours=expected_hours))
        slots = self._window_slots(night, start, end)
        self.assertEqual(len(slots), expected_hours)
        self.assertTrue(
            all(later - earlier == EVERY_HOUR for earlier, later in zip(slots, slots[1:])),
            "slots stay one real hour apart across the transition",
        )

    def test_dst_clocks_set_back_at_ramadan_start(self):
        (instant, before, after), _forward = self._ramadan_transitions()
        # The wall clock goes back: the hour starting at `repeated` happens twice.
        repeated = instant + after
        wall = repeated + timedelta(minutes=30)
        self.assertEqual(local_to_utc(CASABLANCA, wall), wall - before, "first occurrence wins")

        daily = [FixedTime('daily', wall.hour * 60 + wall.minute)]
        first = next_fixed_run(daily, CASABLANCA, instant - timedelta(hours=3))
        self.assertEqual(first, wall - before)
        second = next_fixed_run(daily, CASABLANCA, first)
        self.assertEqual(second, wall + timedelta(days=1) - after, "no second run in the repeated hour")

        self._night_window_check(repeated.date(), 6 + (before - after) // EVERY_HOUR)

    def test_dst_clocks_set_forward_at_ramadan_end(self):
        _back, (instant, before, after) = self._ramadan_transitions()
        # The wall clock jumps forward: the hour starting at `skipped` never happens.
        skipped = instant + before
        wall = skipped + timedelta(minutes=30)
        shifted = local_to_utc(CASABLANCA, wall)
        self.assertEqual(shifted, wall - before)
        self.assertEqual(
            self.local(shifted), (wall + after - before).strftime('%Y-%m-%d %H:%M'),
            "a skipped time is pushed forward by the length of the gap",
        )

        daily = [FixedTime('daily', wall.hour * 60 + wall.minute)]
        self.assertEqual(next_fixed_run(daily, CASABLANCA, instant - timedelta(hours=3)), shifted)

        self._night_window_check(skipped.date(), 6 - (after - before) // EVERY_HOUR)
