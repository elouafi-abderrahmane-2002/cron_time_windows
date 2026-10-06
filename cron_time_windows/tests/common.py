from datetime import datetime
from unittest.mock import patch

import pytz

from odoo import Command
from odoo.tests import TransactionCase
from odoo.tools import mute_logger

CASABLANCA = pytz.timezone('Africa/Casablanca')
LOGGERS = ('odoo.addons.base.models.ir_cron', 'odoo.addons.cron_time_windows.models.ir_cron_scheduler')


class CronTwCase(TransactionCase):
    """Helpers shared by the tests. Wall-clock times are in Africa/Casablanca.

    2026-09-28 is a Monday, outside Ramadan. Only :meth:`run_scheduler`,
    :meth:`run_manually` and :meth:`cron_defaults` depend on the Odoo version.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env.ref('base.model_res_partner')

    @staticmethod
    def utc(local, tz=CASABLANCA):
        """Return the naive UTC instant of an unambiguous wall-clock time ``YYYY-MM-DD HH:MM``."""
        naive = datetime.strptime(local, '%Y-%m-%d %H:%M')
        return tz.localize(naive, is_dst=None).astimezone(pytz.utc).replace(tzinfo=None)

    @staticmethod
    def local(value_utc, tz=CASABLANCA):
        """Return the wall-clock time ``YYYY-MM-DD HH:MM`` of a naive UTC instant."""
        return pytz.utc.localize(value_utc).astimezone(tz).strftime('%Y-%m-%d %H:%M')

    def mock_now(self, value_utc):
        """Return a patcher making every scheduling decision read ``value_utc`` as now."""
        return patch.object(self.registry['ir.cron'], '_tw_now', return_value=value_utc)

    @staticmethod
    def window(hour_from, hour_to, every=0, unit='minutes', action='run', days=()):
        """Return a command creating a time window; ``days`` lists weekday field names."""
        values = {
            'hour_from': hour_from,
            'hour_to': hour_to,
            'action': action,
            'interval_number': every,
            'interval_type': unit,
        }
        values.update(dict.fromkeys(days, True))
        return Command.create(values)

    @staticmethod
    def fixed(frequency, time_of_day, weekday=False, day_of_month=1):
        """Return a command creating a fixed time."""
        return Command.create({
            'frequency': frequency,
            'time_of_day': time_of_day,
            'weekday': weekday,
            'day_of_month': day_of_month,
        })

    def sync_ads_values(self):
        """The "Sync ads" example: 08:00-18:00 every 5 min, 18:00-23:00 every 30, then paused."""
        return {
            'tw_mode': 'windows',
            'tw_tz': 'Africa/Casablanca',
            'tw_outside_action': 'pause',
            'tw_window_ids': [
                self.window(8, 18, every=5),
                self.window(18, 23, every=30),
                self.window(23, 8, action='pause'),
            ],
        }

    def daily_report_values(self):
        """The "Daily report" example: every day at 07:30, plus every Monday at 09:00."""
        return {
            'tw_mode': 'fixed',
            'tw_tz': 'Africa/Casablanca',
            'tw_fixed_ids': [
                self.fixed('daily', 7.5),
                self.fixed('weekly', 9.0, weekday='0'),
            ],
        }

    def cron_defaults(self):
        """Return the values every test cron starts from."""
        return {
            'name': 'cron_time_windows test job',
            'model_id': self.partner_model.id,
            'state': 'code',
            'code': '',
            'user_id': self.env.uid,
            'interval_number': 1,
            'interval_type': 'hours',
            'nextcall': datetime(2026, 1, 1),
            'numbercall': -1,
            'doall': False,
        }

    def create_cron(self, **values):
        """Create a scheduled action on res.partner; ``values`` override the defaults."""
        return self.env['ir.cron'].create({**self.cron_defaults(), **values})

    def _fake_run(self, calls):
        def fake_run(action):
            calls.append(action.id)
        return patch.object(self.registry['ir.actions.server'], 'run', fake_run)

    def run_scheduler(self, cron, now):
        """Process ``cron`` as the cron worker does at ``now``; return how often it ran."""
        calls = []
        with self.mock_now(now), self._fake_run(calls), mute_logger(*LOGGERS):
            job = cron.read(load=None)[0]
            self.env.flush_all()
            self.registry.enter_test_mode(self.cr)
            try:
                with self.registry.cursor() as cr:
                    self.registry['ir.cron']._process_job(self.registry.db_name, cr, job)
            finally:
                self.registry.leave_test_mode()
        cron.invalidate_recordset()
        return len(calls)

    def run_manually(self, cron, now):
        """Click "Run Manually" on ``cron`` at ``now``; return how often it ran."""
        calls = []
        with self.mock_now(now), self._fake_run(calls), mute_logger(*LOGGERS):
            cron.method_direct_trigger()
        cron.invalidate_recordset()
        return len(calls)
