"""Hooks into the Odoo 18.0 scheduler (``odoo/addons/base/models/ir_cron.py``).

One execution runs ``_process_jobs`` -> ``_acquire_one_job`` -> ``_process_job`` ->
``_run_job`` -> ``_callback`` (up to ten batches), then ``_reschedule_later`` or
``_reschedule_asap``:

* :meth:`IrCron._process_job` records, for the duration of one job, whether its server
  action actually ran and for which slot;
* :meth:`IrCron._callback` is the safety net: it skips the server action when the job
  wakes up outside its allowed window, or for a slot it already served;
* :meth:`IrCron._reschedule_later` stores the next execution computed by
  :meth:`IrCron._compute_next_call` instead of adding the native interval, and drops the
  past triggers as the native method does.

``method_direct_trigger`` runs the server action directly, without ``_callback``, so
manual runs bypass the safety net on their own; :meth:`IrCron.method_direct_trigger` then
records the slot they served, as the scheduler would. ``_reschedule_asap`` is left alone: a
partially done job stays ready, and the safety net stops it if its window has ended in
the meantime. Crons in ``native`` mode go through every hook untouched.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

from odoo import fields, models
from odoo.tools import SQL

from .cron_tw_mixin import SKIP_OWNER_HOOK

_logger = logging.getLogger(__name__)


@dataclass
class _JobRun:
    """What happened to the server action during one :meth:`IrCron._process_job`."""

    ran: bool = False
    skipped: bool = False
    #: Latest slot the server action ran for, if the schedule defines one.
    slot: datetime | None = None

    def record_run(self, slot: datetime | None) -> None:
        """Note that the server action ran, for ``slot`` when it is known."""
        self.ran = True
        if slot is not None and (self.slot is None or slot > self.slot):
            self.slot = slot


#: Set by :meth:`IrCron._process_job` while one job is processed, in the worker thread.
_current_job_run: ContextVar[_JobRun | None] = ContextVar(
    'cron_time_windows_job_run', default=None,
)


class IrCron(models.Model):
    _inherit = 'ir.cron'

    def method_direct_trigger(self, *args, **kwargs):
        """Run the job now, whatever the schedule, then use up the current slot."""
        result = super().method_direct_trigger(*args, **kwargs)
        self._tw_after_manual_run(self._tw_now())
        return result

    def _tw_after_manual_run(self, now: datetime) -> None:
        """Record the slot a manual run served and move ``nextcall`` past it, as the
        scheduler does after a run, so that the worker does not run the job again."""
        for cron in self:
            if cron.tw_mode == 'native':
                continue
            vals = {}
            slot = cron._tw_current_slot(now)
            if slot is not None and (not cron.tw_served_until or slot > cron.tw_served_until):
                vals['tw_served_until'] = slot
            nextcall = cron._tw_planned_nextcall(cron.nextcall, now)
            if nextcall is not None and nextcall != cron.nextcall:
                vals['nextcall'] = nextcall
            if vals:
                cron.with_context(**{SKIP_OWNER_HOOK: True}).write(vals)

    @classmethod
    def _process_job(cls, *args, **kwargs):
        """Record what the server action did, for :meth:`_reschedule_later`."""
        token = _current_job_run.set(_JobRun())
        try:
            return super()._process_job(*args, **kwargs)
        finally:
            _current_job_run.reset(token)

    def _callback(self, cron_name, server_action_id, *args, **kwargs):
        """Skip the server action outside the allowed window or for a slot already served."""
        if self.sudo().tw_mode == 'native':
            return super()._callback(cron_name, server_action_id, *args, **kwargs)
        run = _current_job_run.get()
        now = self._tw_now()
        reason = self._tw_skip_reason(now)
        if reason:
            _logger.info("Job %r (%s) skipped: %s.", cron_name, self.id, reason)
            if run is not None:
                run.skipped = True
            return None
        if run is not None:
            run.record_run(self._tw_current_slot(now))
        return super()._callback(cron_name, server_action_id, *args, **kwargs)

    def _reschedule_later(self, job, *args, **kwargs):
        """Store the next execution given by the schedule instead of the native interval.

        The slot the server action ran for is recorded in ``tw_served_until``. When the
        safety net skipped the server action, ``lastcall`` is left alone: jobs read it as
        the point they processed up to.
        """
        if job.get('tw_mode', 'native') == 'native':
            return super()._reschedule_later(job, *args, **kwargs)
        cron = self.browse(job['id'])
        nextcall = cron._tw_planned_nextcall(job['nextcall'], self._tw_now())
        if nextcall is None:
            _logger.warning(
                "Job %r (%s): its schedule gives no next execution, "
                "falling back to the native interval.",
                job.get('cron_name'), job['id'],
            )
            return super()._reschedule_later(job, *args, **kwargs)
        run = _current_job_run.get()
        if run is not None and run.skipped and not run.ran:
            self.env.cr.execute(SQL(
                "UPDATE ir_cron SET nextcall = %s WHERE id = %s",
                nextcall, job['id'],
            ))
        else:
            served = [moment for moment in (job.get('tw_served_until'), run and run.slot) if moment]
            self.env.cr.execute(SQL(
                """
                UPDATE ir_cron
                   SET nextcall = %s, lastcall = %s, tw_served_until = %s
                 WHERE id = %s
                """,
                nextcall, fields.Datetime.now(), max(served, default=None), job['id'],
            ))
        # As the native method does: drop the triggers that woke the job up.
        self.env.cr.execute(SQL(
            """
            DELETE FROM ir_cron_trigger
             WHERE cron_id = %s
               AND call_at < (now() at time zone 'UTC')
            """,
            job['id'],
        ))
        cron.invalidate_recordset(['nextcall', 'lastcall', 'tw_served_until'])
        return None
