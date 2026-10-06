from datetime import timedelta

from odoo.tests import tagged

from .common import CronTwCase


@tagged('post_install', '-at_install')
class TestLegacyScheduler(CronTwCase):
    """Odoo 16.0 / 17.0 only: Repeat Missed and Number of Calls."""

    def test_missed_calls_run_once(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(**self.sync_ads_values())
        # Repeat Missed replays every missed hourly call: the job still runs once.
        cron.write({'doall': True, 'nextcall': self.utc('2026-09-28 07:00')})
        now = self.utc('2026-09-28 10:05') + timedelta(seconds=30)
        self.assertEqual(self.run_scheduler(cron, now), 1)
        self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:10')

    def test_skipped_call_keeps_the_number_of_calls(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(numbercall=3, **self.sync_ads_values())
        cron.nextcall = self.utc('2026-09-28 23:20')
        self.assertEqual(self.run_scheduler(cron, self.utc('2026-09-28 23:30')), 0)
        self.assertEqual(cron.numbercall, 3)
        self.assertTrue(cron.active)
        self.assertEqual(self.local(cron.nextcall), '2026-09-29 08:00')

    def test_missed_calls_use_up_one_call(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(numbercall=5, **self.sync_ads_values())
        # Odoo replays every missed hourly call; the job runs, and counts, only once.
        cron.write({'doall': True, 'nextcall': self.utc('2026-09-28 07:00')})
        now = self.utc('2026-09-28 10:05') + timedelta(seconds=30)
        self.assertEqual(self.run_scheduler(cron, now), 1)
        self.assertEqual(cron.numbercall, 4)
        self.assertTrue(cron.active)

    def test_run_uses_one_call(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(numbercall=3, **self.sync_ads_values())
        cron.nextcall = self.utc('2026-09-28 10:05')
        now = self.utc('2026-09-28 10:05') + timedelta(seconds=30)
        self.assertEqual(self.run_scheduler(cron, now), 1)
        self.assertEqual(cron.numbercall, 2)
