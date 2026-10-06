===========================================================
Cron Time Windows - Business Hours & Fixed Times Scheduler
===========================================================

Restrict each scheduled action (``ir.cron``) to the times of day it may run, with its own
frequency in each time window, or run it at fixed times. Everything is set on the native
form, in a new **Time Windows** tab: the cron keeps running itself, no dispatcher cron is
added, and the minute-level granularity of the scheduler is kept.

The feature is opt-in per cron. The default mode, **Native**, leaves Odoo's standard
behaviour untouched, so the core crons (mail queue, autovacuum...) are never affected.

.. contents::
   :local:

Schedule modes
==============

Native
  Odoo's standard scheduling: every *Execute Every* interval from the next execution
  date.

Time windows
  A list of windows, each with a start, an end, an action and optional weekdays.

  * **Run** windows execute the job at their own interval (minutes or hours). Slots are
    counted from the window start: an 08:00-18:00 window every 5 minutes runs at 08:00,
    08:05... 17:55, whatever time the worker actually picked the job up.
  * **Pause** windows never execute the job.
  * A window that ends before it starts crosses midnight (22:00-06:00) and belongs to the
    day it starts on: a Friday 22:00-06:00 window covers Friday night until Saturday
    06:00. Equal start and end cover 24 hours.
  * No weekday ticked means every day.
  * **Outside the windows**: *Do not run*, or *Run at the default interval*, the cron's
    own *Execute Every* interval counted from the end of the previous window.

  Windows of the same weekday may not overlap, including the part of a window that runs
  past midnight into the next day. An interval longer than its window is allowed, with a
  warning: the job then runs once, at the window start.

Fixed times
  A list of daily, weekly (one weekday) or monthly (one day of the month) times. Several
  lines combine; the earliest upcoming one wins. A monthly line set on a day the month
  lacks runs on the last day of the month (the 31st becomes the 30th in April, the 28th
  or 29th in February).

All times are in the **Schedule Timezone** (``Africa/Casablanca`` by default); the next
execution date is still stored in UTC. The tab shows the next ten executions in that
timezone, or what prevents the schedule from being saved.

Examples
========

Sync ads
  Mode *Time windows*, outside the windows *Do not run*:

  ============  ============  ======  ==============
  Start         End           Action  Every
  ============  ============  ======  ==============
  08:00         18:00         Run     5 minutes
  18:00         23:00         Run     30 minutes
  23:00         08:00         Pause
  ============  ============  ======  ==============

  At 17:57 the next execution is 18:00; at 22:30 it is 08:00 the next morning.

Daily report
  Mode *Fixed times*: *Daily* at 07:30, plus *Weekly* on Monday at 09:00. On a Monday
  the report runs at 07:30 and 09:00, on the other days at 07:30.

Applying time windows to several crons
======================================

Select scheduled actions in the list view, then *Actions > Apply Time Windows*. The
schedule can be:

* defined in the wizard itself;
* loaded from a **time window template** (*Settings > Technical > Automation > Time
  Window Templates*), a named, reusable set of windows or fixed times;
* copied from another scheduled action.

The loaded lines can be adjusted before being applied; they replace the lines of every
selected cron. Choosing *Native* sends the selected crons back to Odoo's scheduling and
keeps their lines for later.

How it works
============

The scheduling logic lives in ``tools/schedule.py``, a pure module with no ORM access.
``ir.cron._compute_next_call(from_dt_utc)`` reads the schedule and returns the first
execution strictly after ``from_dt_utc``, in naive UTC, or ``None`` in native mode.

``models/ir_cron_scheduler.py`` is the only file that differs between Odoo versions. It
hooks into the scheduler at three points:

After an execution
  The next execution date comes from ``_compute_next_call`` instead of the native
  interval. A job acquired ahead of its planned slot (a manual run, a trigger) keeps that
  slot, as native Odoo does.

Before the server action (safety net)
  For triggers and for a ``nextcall`` changed elsewhere: the server action is skipped
  when the job wakes up outside its allowed window, or for a slot it already served. A
  job runs at most once per slot: the slot must be later than ``tw_served_until``, the
  latest slot served, or the last change of the schedule if later. A skipped execution
  leaves ``lastcall`` alone, since jobs read it as the point they processed up to.

Run Manually
  Always executes, whatever the schedule.

Changing the mode, the timezone, the windows, the fixed times, the interval or the active
flag recomputes the next execution at once, as does changing a window or a fixed time
directly.

Daylight saving time
--------------------

Window boundaries and fixed times follow the wall clock of the schedule timezone; the
slots inside a window are spaced in real time, so "every 5 minutes" keeps meaning 300
seconds across a change. A wall-clock time that happens twice (clocks set back) resolves
to its first occurrence, and runs once; a time that never happens (clocks set forward)
is pushed forward by the length of the gap, so 02:30 becomes 03:30.

In Morocco the clocks go from UTC+1 to UTC+0 for Ramadan, then back. On the night the
clocks go back, a 00:00-06:00 window lasts seven real hours; on the night they go
forward, five.

Security
========

The new models are restricted to *Settings / Administration* (``base.group_system``).
Every rule (ranges, intervals, overlaps, required weekday or day of month, a schedule
that never runs) is enforced server-side, whatever the client.

Known limits
============

* A trigger (``ir.cron._trigger()``) that fires in a pause window, or within a slot
  already served, is dropped: the job runs at its next slot instead.
* A window slot is not caught up once its window has closed. If the worker picks up the
  17:55 slot of an 08:00-18:00 window only at 18:00:30 and nothing may run after 18:00,
  the execution is skipped and the job waits for the next morning. That is the point of
  a pause. When a running window follows, its own 18:00 slot runs as usual. Fixed times,
  on the other hand, catch up once, as native Odoo does.
* A job skipped by the safety net counts as a success for the native failure counter.

Tests
=====

::

    odoo-bin -d <database> -i cron_time_windows --test-tags /cron_time_windows --stop-after-init

The DST tests read the Ramadan transitions of ``Africa/Casablanca`` from the installed
``pytz`` and are skipped if it knows none.

Credits
=======

Author: WebThreenity, Odoo implementation and development (https://webthreenity.netlify.app).
License: LGPL-3.
