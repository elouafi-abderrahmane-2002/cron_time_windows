"""Building blocks shared by every model that owns time windows.

Three models own them: ``ir.cron`` itself, the reusable ``cron.tw.template`` and the
transient ``cron.tw.wizard``. They inherit :class:`CronTwSettingsMixin` and keep their
lines in models built on :class:`CronTwWindowMixin` and :class:`CronTwFixedMixin`.
Every owner names its lines ``tw_window_ids`` and ``tw_fixed_ids``.
"""

from __future__ import annotations

from datetime import timedelta

import pytz

from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.base.models.res_partner import _tz_get

from ..tools import schedule

DEFAULT_TIMEZONE = 'Africa/Casablanca'

#: Context key set while an owner writes its own lines: the line hooks then leave the
#: validation and the ``nextcall`` recomputation to the owner, which runs them once.
SKIP_OWNER_HOOK = 'cron_time_windows_skip_owner_hook'

WEEKDAY_FIELDS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
WINDOW_CONFIG_FIELDS = (
    'sequence', 'hour_from', 'hour_to', 'action', 'interval_number', 'interval_type',
    *WEEKDAY_FIELDS,
)
FIXED_CONFIG_FIELDS = ('sequence', 'frequency', 'time_of_day', 'weekday', 'day_of_month')

TW_MODES = [
    ('native', 'Native'),
    ('windows', 'Time windows'),
    ('fixed', 'Fixed times'),
]
OUTSIDE_ACTIONS = [
    ('pause', 'Do not run'),
    ('default_interval', 'Run at the default interval'),
]
WEEKDAYS = [
    ('0', 'Monday'),
    ('1', 'Tuesday'),
    ('2', 'Wednesday'),
    ('3', 'Thursday'),
    ('4', 'Friday'),
    ('5', 'Saturday'),
    ('6', 'Sunday'),
]


def hours_to_minutes(hours: float) -> int:
    """Convert a ``float_time`` value (8.5 stands for 08:30) to minutes after midnight."""
    return min(max(round((hours or 0.0) * 60), 0), schedule.MINUTES_PER_DAY - 1)


def format_hours(hours: float) -> str:
    """Format a ``float_time`` value as ``HH:MM``, out-of-range values included."""
    return '%02d:%02d' % divmod(round((hours or 0.0) * 60), 60)


class CronTwSettingsMixin(models.AbstractModel):
    """Schedule settings and their validation, shared by crons, templates and the wizard."""

    _name = 'cron.tw.settings.mixin'
    _description = 'Cron Time Windows Settings'

    tw_mode = fields.Selection(
        TW_MODES,
        string='Schedule Mode',
        required=True,
        default='native',
        help="Native: Odoo's standard scheduling, untouched.\n"
             "Time windows: restrict the job to time windows, each with its own frequency.\n"
             "Fixed times: run the job at fixed times, daily, weekly or monthly.",
    )
    tw_tz = fields.Selection(
        _tz_get,
        string='Schedule Timezone',
        required=True,
        default=DEFAULT_TIMEZONE,
        help="Timezone of the time windows and fixed times. Daylight saving changes are "
             "taken into account; the next execution date is still stored in UTC.",
    )
    tw_outside_action = fields.Selection(
        OUTSIDE_ACTIONS,
        string='Outside the Windows',
        required=True,
        default='pause',
        help="What happens at times that no window covers: the job does not run, or it "
             "runs at the scheduled action's own interval (Execute Every), counted from "
             "the end of the previous window.",
    )

    def _tw_timezone(self) -> pytz.BaseTzInfo:
        """Return the pytz timezone of the schedule, UTC if the name is unknown."""
        self.ensure_one()
        try:
            return pytz.timezone(self.tw_tz or 'UTC')
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _tw_window_specs(self) -> list[schedule.DailyWindow]:
        """Return the time windows as engine values, in display order."""
        self.ensure_one()
        return [window._tw_spec() for window in self.tw_window_ids]

    def _tw_fixed_specs(self) -> list[schedule.FixedTime]:
        """Return the fixed times as engine values."""
        self.ensure_one()
        return [line._tw_spec() for line in self.tw_fixed_ids]

    def _tw_config_errors(self) -> list[str]:
        """Return the blocking problems of the schedule, as translated messages.

        Only the lines of the active mode are checked: switching a cron back to
        ``native`` keeps its time windows without validating them.
        """
        self.ensure_one()
        if self.tw_mode == 'windows':
            return self._tw_window_errors()
        if self.tw_mode == 'fixed':
            return self._tw_fixed_errors()
        return []

    def _tw_window_errors(self) -> list[str]:
        """Return the problems of a ``windows`` schedule, overlaps included."""
        windows = self.tw_window_ids
        if not windows:
            return [self.env._("Add at least one time window, or choose another mode.")]
        errors = [error for window in windows for error in window._tw_line_errors()]
        if self.tw_outside_action == 'pause' and not any(window.action == 'run' for window in windows):
            errors.append(self.env._(
                "Every window pauses the job and nothing runs outside the windows: "
                "the job would never run."
            ))
        day_names = dict(self.env['cron.tw.fixed']._fields['weekday']._description_selection(self.env))
        for first, second, weekday in schedule.find_overlaps(self._tw_window_specs()):
            errors.append(self.env._(
                "Windows %(first)s and %(second)s overlap on %(day)s.",
                first=windows[first]._tw_label(),
                second=windows[second]._tw_label(),
                day=day_names[str(weekday)],
            ))
        return errors

    def _tw_fixed_errors(self) -> list[str]:
        """Return the problems of a ``fixed`` schedule."""
        lines = self.tw_fixed_ids
        if not lines:
            return [self.env._("Add at least one fixed time, or choose another mode.")]
        return [error for line in lines for error in line._tw_line_errors()]

    def _tw_config_warnings(self) -> list[str]:
        """Return the non-blocking remarks about the schedule, as translated messages."""
        self.ensure_one()
        if self.tw_mode != 'windows':
            return []
        return [warning for window in self.tw_window_ids for warning in window._tw_line_warnings()]

    def _tw_check_config(self) -> None:
        """Raise a :class:`ValidationError` listing the problems of each schedule."""
        for record in self:
            errors = record._tw_config_errors()
            if errors:
                raise ValidationError("\n".join(errors))

    def _tw_after_lines_change(self) -> None:
        """React to lines created, changed or deleted outside a write on their owner."""
        self._tw_check_config()

    def _tw_copy_vals(self) -> dict:
        """Return the values that give another owner the same schedule, lines replaced."""
        self.ensure_one()
        return {
            'tw_mode': self.tw_mode,
            'tw_tz': self.tw_tz,
            'tw_outside_action': self.tw_outside_action,
            'tw_window_ids': [
                Command.clear(),
                *(Command.create(window._tw_copy_vals()) for window in self.tw_window_ids),
            ],
            'tw_fixed_ids': [
                Command.clear(),
                *(Command.create(line._tw_copy_vals()) for line in self.tw_fixed_ids),
            ],
        }


class CronTwLineMixin(models.AbstractModel):
    """Common behaviour of window and fixed-time lines: ordering, copy and owner hooks."""

    _name = 'cron.tw.line.mixin'
    _description = 'Cron Time Windows Line'
    _order = 'sequence, id'

    #: Name of the many2one to the record that owns the line.
    _tw_owner_field = ''
    #: Fields copied along when the line is given to another owner.
    _tw_config_fields: tuple[str, ...] = ()

    sequence = fields.Integer(default=10)

    def _tw_owners(self) -> models.BaseModel:
        """Return the records owning the lines of ``self``."""
        return self.mapped(self._tw_owner_field)

    def _tw_copy_vals(self) -> dict:
        """Return the configuration values of the line, without its owner."""
        self.ensure_one()
        return {name: self[name] for name in self._tw_config_fields}

    def _tw_notify_owners(self, owners: models.BaseModel) -> None:
        """Let ``owners`` react to a change of their lines, unless they drive it."""
        if owners and not self.env.context.get(SKIP_OWNER_HOOK):
            owners._tw_after_lines_change()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._tw_notify_owners(lines._tw_owners())
        return lines

    def write(self, vals):
        owners = self._tw_owners()
        result = super().write(vals)
        self._tw_notify_owners(owners | self._tw_owners())
        return result

    def unlink(self):
        owners = self._tw_owners()
        result = super().unlink()
        self._tw_notify_owners(owners.exists())
        return result


class CronTwWindowMixin(models.AbstractModel):
    """A time window: bounds in wall-clock time, an action, an interval and weekdays."""

    _name = 'cron.tw.window.mixin'
    _inherit = 'cron.tw.line.mixin'
    _description = 'Cron Time Window'
    _order = 'sequence, hour_from, id'
    _tw_config_fields = WINDOW_CONFIG_FIELDS

    hour_from = fields.Float(string='Start', required=True, default=8.0)
    hour_to = fields.Float(
        string='End',
        required=True,
        default=18.0,
        help="A window that ends before it starts crosses midnight (22:00-06:00) and "
             "belongs to the day it starts on. Equal start and end cover 24 hours.",
    )
    action = fields.Selection(
        [('run', 'Run'), ('pause', 'Pause')],
        string='Action',
        required=True,
        default='run',
        help="Run: execute the job at the window interval. Pause: never execute it during "
             "the window.",
    )
    interval_number = fields.Integer(
        string='Every',
        default=5,
        help="Interval between two executions, counted from the window start.",
    )
    interval_type = fields.Selection(
        [('minutes', 'Minutes'), ('hours', 'Hours')],
        string='Unit',
        default='minutes',
    )
    monday = fields.Boolean(string='Mon')
    tuesday = fields.Boolean(string='Tue')
    wednesday = fields.Boolean(string='Wed')
    thursday = fields.Boolean(string='Thu')
    friday = fields.Boolean(string='Fri')
    saturday = fields.Boolean(string='Sat')
    sunday = fields.Boolean(string='Sun')
    interval_exceeds_window = fields.Boolean(
        string='Interval Longer Than Window',
        compute='_compute_interval_exceeds_window',
        help="The interval is longer than the window: the job runs only once, at the window start.",
    )

    @api.depends('hour_from', 'hour_to', 'action', 'interval_number', 'interval_type')
    def _compute_interval_exceeds_window(self):
        for window in self:
            step = window._tw_step()
            duration = timedelta(minutes=window._tw_spec().duration)
            window.interval_exceeds_window = step is not None and step > duration

    @api.constrains('hour_from', 'hour_to', 'action', 'interval_number', 'interval_type')
    def _check_window_values(self):
        for window in self:
            errors = window._tw_line_errors()
            if errors:
                raise ValidationError("\n".join(errors))

    @api.onchange('hour_from', 'hour_to', 'action', 'interval_number', 'interval_type')
    def _onchange_interval_exceeds_window(self):
        warnings = self._tw_line_warnings()
        if warnings:
            return {'warning': {
                'title': self.env._("Interval longer than the window"),
                'message': warnings[0],
            }}
        return None

    def _tw_step(self) -> timedelta | None:
        """Return the interval of a running window, ``None`` for a pause or no interval."""
        self.ensure_one()
        if self.action != 'run' or self.interval_number <= 0:
            return None
        if self.interval_type not in ('minutes', 'hours'):
            return None
        return timedelta(**{self.interval_type: self.interval_number})

    def _tw_spec(self) -> schedule.DailyWindow:
        """Return the window as an engine value; no weekday ticked means every day."""
        self.ensure_one()
        weekdays = frozenset(index for index, name in enumerate(WEEKDAY_FIELDS) if self[name])
        return schedule.DailyWindow(
            start=hours_to_minutes(self.hour_from),
            end=hours_to_minutes(self.hour_to),
            step=self._tw_step(),
            weekdays=weekdays or schedule.ALL_WEEKDAYS,
        )

    def _tw_label(self) -> str:
        """Return the window bounds, ``08:00-18:00``, for messages."""
        self.ensure_one()
        return f"{format_hours(self.hour_from)}-{format_hours(self.hour_to)}"

    def _tw_line_errors(self) -> list[str]:
        """Return the blocking problems of the window alone, as translated messages."""
        self.ensure_one()
        errors = []
        if not (0 <= self.hour_from < 24 and 0 <= self.hour_to < 24):
            errors.append(self.env._(
                "Window %(window)s: start and end must lie between 00:00 and 23:59 "
                "(write midnight as 00:00).",
                window=self._tw_label(),
            ))
        if self.action == 'run' and self._tw_step() is None:
            errors.append(self.env._(
                "Window %(window)s: a running window needs an interval greater than zero.",
                window=self._tw_label(),
            ))
        return errors

    def _tw_line_warnings(self) -> list[str]:
        """Return the non-blocking remarks about the window, as translated messages."""
        self.ensure_one()
        if not self.interval_exceeds_window:
            return []
        return [self.env._(
            "Window %(window)s: the interval is longer than the window, so the job runs "
            "only once, at %(start)s.",
            window=self._tw_label(),
            start=format_hours(self.hour_from),
        )]


class CronTwFixedMixin(models.AbstractModel):
    """A fixed execution time: daily, weekly on one weekday, or monthly on one day."""

    _name = 'cron.tw.fixed.mixin'
    _inherit = 'cron.tw.line.mixin'
    _description = 'Cron Fixed Time'
    _order = 'sequence, time_of_day, id'
    _tw_config_fields = FIXED_CONFIG_FIELDS

    frequency = fields.Selection(
        [('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')],
        string='Frequency',
        required=True,
        default='daily',
    )
    time_of_day = fields.Float(string='Time', required=True, default=8.0)
    weekday = fields.Selection(WEEKDAYS, string='Weekday', help="Day of the week of a weekly line.")
    day_of_month = fields.Integer(
        string='Day of Month',
        default=1,
        help="Day of a monthly line. In shorter months the last day of the month is used "
             "instead: 31 becomes 30 in April, 28 or 29 in February.",
    )

    @api.constrains('frequency', 'time_of_day', 'weekday', 'day_of_month')
    def _check_fixed_values(self):
        for line in self:
            errors = line._tw_line_errors()
            if errors:
                raise ValidationError("\n".join(errors))

    def _tw_spec(self) -> schedule.FixedTime:
        """Return the fixed time as an engine value."""
        self.ensure_one()
        return schedule.FixedTime(
            frequency=self.frequency,
            minute=hours_to_minutes(self.time_of_day),
            weekday=int(self.weekday) if self.weekday else None,
            day_of_month=self.day_of_month or None,
        )

    def _tw_label(self) -> str:
        """Return the time of the line, ``07:30``, for messages."""
        self.ensure_one()
        return format_hours(self.time_of_day)

    def _tw_line_errors(self) -> list[str]:
        """Return the blocking problems of the line, as translated messages."""
        self.ensure_one()
        errors = []
        if not 0 <= self.time_of_day < 24:
            errors.append(self.env._(
                "Fixed time %(time)s: the time must lie between 00:00 and 23:59.",
                time=self._tw_label(),
            ))
        if self.frequency == 'weekly' and not self.weekday:
            errors.append(self.env._(
                "Fixed time %(time)s: a weekly line needs a weekday.",
                time=self._tw_label(),
            ))
        if self.frequency == 'monthly' and not 1 <= self.day_of_month <= 31:
            errors.append(self.env._(
                "Fixed time %(time)s: the day of the month must be between 1 and 31.",
                time=self._tw_label(),
            ))
        return errors
