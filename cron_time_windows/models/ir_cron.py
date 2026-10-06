"""Time windows and fixed times on scheduled actions: fields, computation, validation.

The hooks into the scheduler itself live in ``ir_cron_scheduler.py``, the only part of
the module that differs between Odoo versions.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.tools.misc import format_datetime

from ..tools import schedule
from .cron_tw_mixin import FIXED_CONFIG_FIELDS, SKIP_OWNER_HOOK, WINDOW_CONFIG_FIELDS

#: Number of executions listed in the preview.
PREVIEW_COUNT = 10

#: Writing one of these fields on a cron recomputes its next execution date.
TW_FIELDS = frozenset({
    'tw_mode', 'tw_tz', 'tw_outside_action', 'tw_window_ids', 'tw_fixed_ids',
    'interval_number', 'interval_type', 'active',
})


def _format_utc_offset(offset: timedelta) -> str:
    """Format a UTC offset as ``UTC+01:00``."""
    minutes = int(offset.total_seconds() // 60)
    hours, minutes = divmod(abs(minutes), 60)
    sign = '-' if offset < timedelta(0) else '+'
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


class IrCron(models.Model):
    _name = 'ir.cron'
    _inherit = ['ir.cron', 'cron.tw.settings.mixin']

    tw_window_ids = fields.One2many('cron.tw.window', 'cron_id', string='Time Windows', copy=True)
    tw_fixed_ids = fields.One2many('cron.tw.fixed', 'cron_id', string='Fixed Times', copy=True)
    tw_served_until = fields.Datetime(
        string='Served Up To',
        readonly=True,
        copy=False,
        help="Latest slot the job ran for, or the last change of the schedule if later. "
             "Slots up to this date never run again, even when a trigger wakes the job up.",
    )
    tw_preview = fields.Text(
        string='Next Executions',
        compute='_compute_tw_preview',
        help="The next executions, in the schedule timezone, or what prevents the "
             "schedule from being saved.",
    )
    tw_nextcall_local = fields.Char(
        string='Next Execution (Local)',
        compute='_compute_tw_nextcall_local',
        help="Next execution date, in the schedule timezone.",
    )

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    @api.depends(
        'tw_mode', 'tw_tz', 'tw_outside_action', 'interval_number', 'interval_type',
        *(f'tw_window_ids.{name}' for name in WINDOW_CONFIG_FIELDS),
        *(f'tw_fixed_ids.{name}' for name in FIXED_CONFIG_FIELDS),
    )
    def _compute_tw_preview(self):
        now = self._tw_now()
        for cron in self:
            if cron.tw_mode == 'native':
                cron.tw_preview = False
                continue
            errors = cron._tw_config_errors()
            if errors:
                cron.tw_preview = "\n".join(errors)
                continue
            lines = cron._tw_config_warnings()
            lines += [cron._tw_format(moment) for moment in cron._tw_upcoming(now)]
            cron.tw_preview = "\n".join(lines)

    @api.depends('nextcall', 'tw_tz')
    def _compute_tw_nextcall_local(self):
        for cron in self:
            cron.tw_nextcall_local = cron._tw_format(cron.nextcall) if cron.nextcall else False

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    @api.model
    def _tw_now(self) -> datetime:
        """Return the current time as a naive UTC datetime.

        Every scheduling decision reads the clock here, which gives tests one single
        entry point to patch.
        """
        return fields.Datetime.now()

    def _tw_outside_step(self) -> schedule.Step | None:
        """Return the step used outside the windows, ``None`` when the job pauses there."""
        self.ensure_one()
        if self.tw_outside_action != 'default_interval' or self.interval_number <= 0:
            return None
        if self.interval_type == 'months':
            return relativedelta(months=self.interval_number)
        return timedelta(**{self.interval_type: self.interval_number})

    def _compute_next_call(self, from_dt_utc: datetime) -> datetime | None:
        """Return the first execution strictly after ``from_dt_utc``, in naive UTC.

        The method is pure: it reads the schedule and writes nothing. It returns
        ``None`` in ``native`` mode, where Odoo's own rescheduling applies, and for a
        schedule that never runs.

        * ``windows``: inside a running window, the next slot of that window, slots
          being spaced by the window interval from the window start; past the window
          end, or in a pausing window, the first slot of the next running span. Outside
          every window, ``tw_outside_action`` decides: no execution, or the cron's own
          interval counted from the end of the previous window.
        * ``fixed``: the earliest upcoming time across all fixed lines.
        """
        self.ensure_one()
        cron = self.sudo()
        if cron.tw_mode == 'windows':
            return schedule.next_window_run(
                cron._tw_window_specs(), cron._tw_timezone(), from_dt_utc, cron._tw_outside_step(),
            )
        if cron.tw_mode == 'fixed':
            return schedule.next_fixed_run(cron._tw_fixed_specs(), cron._tw_timezone(), from_dt_utc)
        return None

    def _tw_current_slot(self, at_utc: datetime) -> datetime | None:
        """Return the slot that ``at_utc`` belongs to, ``None`` if the job may not run.

        In ``windows`` mode that is the latest slot of the running span containing the
        instant; in ``fixed`` mode, the latest fixed time at or before it, so that a
        worker that was down still catches up once, as native Odoo does.
        """
        self.ensure_one()
        cron = self.sudo()
        if cron.tw_mode == 'windows':
            return schedule.current_window_slot(
                cron._tw_window_specs(), cron._tw_timezone(), at_utc, cron._tw_outside_step(),
            )
        if cron.tw_mode == 'fixed':
            return schedule.current_fixed_slot(cron._tw_fixed_specs(), cron._tw_timezone(), at_utc)
        return None

    def _tw_skip_reason(self, at_utc: datetime) -> str | None:
        """Return why the job must not run at ``at_utc``, or ``None`` when it may.

        A job runs at most once per slot: the slot must be later than
        ``tw_served_until``, the latest slot served or the last change of the schedule.
        Slots are compared with slots, never with ``lastcall``, which may come from the
        database clock. The reason goes to the server log, hence it is not translated.
        """
        self.ensure_one()
        cron = self.sudo()
        if cron.tw_mode == 'native':
            return None
        slot = cron._tw_current_slot(at_utc)
        if slot is None:
            return "outside its allowed time window"
        served = cron.tw_served_until
        if served and served >= slot:
            return f"the {slot} UTC slot is already served (served up to {served} UTC)"
        return None

    def _tw_planned_nextcall(self, planned: datetime | None, now: datetime) -> datetime | None:
        """Return the next execution to store after a job ran or was skipped.

        A job acquired ahead of its planned slot, by a manual run or a trigger, keeps that
        slot, as native Odoo does; otherwise the schedule gives the next one.
        """
        self.ensure_one()
        if planned and planned > now:
            return planned
        return self._compute_next_call(now)

    def _tw_upcoming(self, after_utc: datetime, count: int = PREVIEW_COUNT) -> list[datetime]:
        """Return up to ``count`` executions after ``after_utc``, in naive UTC."""
        self.ensure_one()
        moments = []
        moment = after_utc
        while len(moments) < count:
            moment = self._compute_next_call(moment)
            if moment is None:
                break
            moments.append(moment)
        return moments

    def _tw_format(self, value_utc: datetime) -> str:
        """Format a UTC instant in the schedule timezone: ``Mon 2026-09-28 08:00 (UTC+01:00)``."""
        self.ensure_one()
        tz = self._tw_timezone()
        offset = pytz.utc.localize(value_utc).astimezone(tz).utcoffset() or timedelta(0)
        text = format_datetime(self.env, value_utc, tz=tz.zone, dt_format='EEE yyyy-MM-dd HH:mm')
        return f"{text} ({_format_utc_offset(offset)})"

    def _tw_recompute_nextcall(self) -> None:
        """Store the next execution of every non-native cron of ``self``, from now on."""
        now = self._tw_now()
        for cron in self:
            if cron.tw_mode == 'native':
                continue
            nextcall = cron._compute_next_call(now)
            if nextcall is None:
                continue
            # Slots planned before the change are never served, even by a trigger.
            cron.with_context(**{SKIP_OWNER_HOOK: True}).write({
                'nextcall': nextcall,
                'tw_served_until': now,
            })

    # ------------------------------------------------------------------
    # Validation and change hooks
    # ------------------------------------------------------------------

    @api.constrains(
        'tw_mode', 'tw_tz', 'tw_outside_action', 'tw_window_ids', 'tw_fixed_ids',
        'interval_number', 'interval_type',
    )
    def _check_tw_schedule(self):
        self._tw_check_config()

    def _tw_config_errors(self) -> list[str]:
        errors = super()._tw_config_errors()
        if (
            not errors
            and self.tw_mode != 'native'
            and self._compute_next_call(self._tw_now()) is None
        ):
            errors.append(_("With this schedule the job would never run."))
        return errors

    def _tw_after_lines_change(self) -> None:
        super()._tw_after_lines_change()
        self._tw_recompute_nextcall()

    @api.model_create_multi
    def create(self, vals_list):
        crons = super(IrCron, self.with_context(**{SKIP_OWNER_HOOK: True})).create(vals_list)
        crons = crons.with_env(self.env)
        crons._tw_recompute_nextcall()
        return crons

    def write(self, vals):
        if self.env.context.get(SKIP_OWNER_HOOK) or not TW_FIELDS.intersection(vals):
            return super().write(vals)
        result = super(IrCron, self.with_context(**{SKIP_OWNER_HOOK: True})).write(vals)
        self._tw_recompute_nextcall()
        return result
