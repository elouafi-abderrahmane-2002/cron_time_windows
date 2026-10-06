"""Hooks into the Odoo 16.0 / 17.0 scheduler (``odoo/addons/base/models/ir_cron.py``).

``_process_job`` computes the next execution itself, calls ``_callback`` once (or once
per missed call with *Repeat Missed*), then writes ``nextcall``, ``numbercall``,
``lastcall`` and ``active`` in one statement:

* :meth:`IrCron._process_job` lets Odoo run the job, then overwrites ``nextcall`` with
  the one given by :meth:`IrCron._compute_next_call`; when the safety net skipped every
  call, it also restores ``lastcall``, ``numbercall`` and ``active``;
* :meth:`IrCron._callback` is the safety net: it skips the server action when the job
  wakes up outside its allowed window or for a slot it already served, and runs it at
  most once per job even when Odoo replays missed calls.

``method_direct_trigger`` runs the server action directly, without ``_callback``, so
manual runs bypass the safety net on their own; :meth:`IrCron.method_direct_trigger` then
records the slot they served, as the scheduler would. Crons in ``native`` mode go through
every hook untouched.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

from odoo import api, models

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
    def _process_job(cls, db, cron_cr, job, *args, **kwargs):
        """Run the job as Odoo does, then store the next execution given by the schedule."""
        run = _JobRun()
        token = _current_job_run.set(run)
        try:
            result = super()._process_job(db, cron_cr, job, *args, **kwargs)
        finally:
            _current_job_run.reset(token)
        # Odoo deactivates a job whose interval is not positive and stops there.
        if job.get('tw_mode', 'native') != 'native' and job['interval_number'] > 0:
            api.Environment(cron_cr, job['user_id'], {})[cls._name]._tw_store_schedule(job, run)
        return result

    @api.model
    def _tw_store_schedule(self, job: dict, run: _JobRun) -> None:
        """Overwrite what Odoo just wrote for ``job`` with the schedule's own values.

        The slot the server action ran for is recorded in ``tw_served_until``. When the
        safety net skipped every call, ``lastcall``, ``numbercall`` and ``active`` get
        their previous values back: jobs read ``lastcall`` as the point they processed up
        to, and a skipped call must not use up the number of calls.
        """
        cron = self.browse(job['id'])
        nextcall = cron._tw_planned_nextcall(job['nextcall'], self._tw_now())
        if nextcall is None:
            _logger.warning(
                "Job %r (%s): its schedule gives no next execution, "
                "keeping the native one.",
                job.get('cron_name'), job['id'],
            )
            return
        if run.skipped and not run.ran:
            self.env.cr.execute(
                """
                UPDATE ir_cron
                   SET nextcall = %s, lastcall = %s, numbercall = %s, active = %s
                 WHERE id = %s
                """,
                [nextcall, job.get('lastcall') or None, job['numbercall'], job['active'], job['id']],
            )
        else:
            served = [moment for moment in (job.get('tw_served_until'), run.slot) if moment]
            # The job ran once: replayed calls skipped by the safety net use up no call.
            calls_left = max(job['numbercall'] - 1, -1)
            self.env.cr.execute(
                """
                UPDATE ir_cron
                   SET nextcall = %s, tw_served_until = %s, numbercall = %s, active = %s
                 WHERE id = %s
                """,
                [nextcall, max(served, default=None), calls_left,
                 job['active'] and bool(calls_left), job['id']],
            )
        cron.invalidate_recordset(['nextcall', 'lastcall', 'numbercall', 'active', 'tw_served_until'])

    @api.model
    def _callback(self, cron_name, server_action_id, job_id, *args, **kwargs):
        """Skip the server action outside the allowed window, for a slot already served,
        or when it already ran during this job."""
        cron = self.browse(job_id)
        if cron.sudo().tw_mode == 'native':
            return super()._callback(cron_name, server_action_id, job_id, *args, **kwargs)
        run = _current_job_run.get()
        if run is not None and run.ran:
            _logger.info("Job %r (%s) skipped: it already ran during this pass.", cron_name, job_id)
            return None
        now = cron._tw_now()
        reason = cron._tw_skip_reason(now)
        if reason:
            _logger.info("Job %r (%s) skipped: %s.", cron_name, job_id, reason)
            if run is not None:
                run.skipped = True
            return None
        if run is not None:
            run.record_run(cron._tw_current_slot(now))
        return super()._callback(cron_name, server_action_id, job_id, *args, **kwargs)
