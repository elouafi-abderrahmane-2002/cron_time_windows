from datetime import datetime, timedelta

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CronTwCase


@tagged('post_install', '-at_install')
class TestIrCronTimeWindows(CronTwCase):
    """Time windows on ir.cron: validation, nextcall hooks and the scheduler itself."""

    def assertInvalid(self, **values):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.create_cron(**values)

    # ------------------------------------------------------------------
    # Native crons
    # ------------------------------------------------------------------

    def test_native_is_the_default_and_untouched(self):
        self.assertEqual(self.env['ir.cron'].default_get(['tw_mode'])['tw_mode'], 'native')
        nextcall = datetime(2030, 1, 1, 12, 0)
        cron = self.create_cron(nextcall=nextcall)
        cron.write({'interval_number': 3, 'tw_window_ids': [self.window(8, 18, every=5)]})
        self.assertEqual(cron.nextcall, nextcall)
        self.assertIsNone(cron._compute_next_call(nextcall))
        self.assertIsNone(cron._tw_skip_reason(nextcall))
        self.assertFalse(cron.tw_preview)

    # ------------------------------------------------------------------
    # nextcall on create and write
    # ------------------------------------------------------------------

    def test_create_stores_nextcall_in_utc(self):
        with self.mock_now(self.utc('2026-09-28 10:02')):
            cron = self.create_cron(**self.sync_ads_values())
        self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:05')
        self.assertEqual(cron.nextcall, datetime(2026, 9, 28, 9, 5), "Casablanca is UTC+1 outside Ramadan")
        self.assertEqual(cron.tw_served_until, self.utc('2026-09-28 10:02'))

    def test_compute_next_call_with_mocked_time(self):
        with self.mock_now(self.utc('2026-09-28 17:50')):
            cron = self.create_cron(**self.sync_ads_values())
            now = cron._tw_now()
            self.assertEqual(self.local(cron._compute_next_call(now)), '2026-09-28 17:55')
            self.assertEqual(self.local(cron.nextcall), '2026-09-28 17:55')
        self.assertEqual(
            self.local(cron._compute_next_call(self.utc('2026-09-28 22:31'))), '2026-09-29 08:00',
        )

    def test_changes_recompute_nextcall(self):
        with self.mock_now(self.utc('2026-09-28 10:02')):
            cron = self.create_cron(**self.sync_ads_values())
            day_window = cron.tw_window_ids[0]
            self.assertEqual(day_window.hour_from, 8.0)

            day_window.interval_number = 15
            self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:15', "direct window write")

            cron.write({'tw_window_ids': [Command.update(day_window.id, {'interval_number': 20})]})
            self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:20', "window written through the cron")

            cron.tw_tz = 'UTC'
            self.assertEqual(cron.nextcall, datetime(2026, 9, 28, 9, 20), "10:02 Casablanca is 09:02 UTC")

            cron.write({'tw_mode': 'fixed', 'tw_fixed_ids': [self.fixed('daily', 7.5)]})
            self.assertEqual(cron.nextcall, datetime(2026, 9, 29, 7, 30))

    def test_swapping_windows_in_one_write(self):
        with self.mock_now(self.utc('2026-09-28 10:02')):
            cron = self.create_cron(tw_mode='windows', tw_window_ids=[
                self.window(8, 12, every=5),
                self.window(12, 18, every=30),
            ])
            morning, afternoon = cron.tw_window_ids
            # Each intermediate state overlaps; the final one does not.
            cron.write({'tw_window_ids': [
                Command.update(morning.id, {'hour_from': 12, 'hour_to': 18}),
                Command.update(afternoon.id, {'hour_from': 8, 'hour_to': 12}),
            ]})
            self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:30')

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def test_window_constraints(self):
        self.assertInvalid(tw_mode='windows', tw_window_ids=[
            self.window(8, 18, every=5), self.window(17, 20, every=10),
        ])
        self.assertInvalid(tw_mode='windows', tw_window_ids=[
            self.window(22, 6, every=60, days=['friday']), self.window(5, 9, every=60, days=['saturday']),
        ])
        self.assertInvalid(tw_mode='windows', tw_window_ids=[self.window(8, 18, every=0)])
        self.assertInvalid(tw_mode='windows', tw_window_ids=[self.window(8, 24, every=5)])
        self.assertInvalid(tw_mode='windows', tw_window_ids=[])
        self.assertInvalid(tw_mode='windows', tw_outside_action='pause',
                           tw_window_ids=[self.window(23, 8, action='pause')])
        # A week fully paused never runs, even with the default interval outside the windows.
        self.assertInvalid(tw_mode='windows', tw_outside_action='default_interval',
                           tw_window_ids=[self.window(0, 0, action='pause')])

        self.create_cron(tw_mode='windows', tw_window_ids=[
            self.window(8, 18, every=5, days=['monday']), self.window(9, 10, every=5, days=['tuesday']),
        ])
        self.create_cron(tw_mode='windows', tw_outside_action='default_interval',
                         tw_window_ids=[self.window(23, 8, action='pause')])
        self.create_cron(**self.sync_ads_values())

    def test_window_constraints_on_direct_line_changes(self):
        cron = self.create_cron(**self.sync_ads_values())
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['cron.tw.window'].create({
                'cron_id': cron.id, 'hour_from': 9, 'hour_to': 10,
                'interval_number': 5, 'interval_type': 'minutes',
            })
        with self.assertRaises(ValidationError), self.cr.savepoint():
            cron.tw_window_ids.unlink()

    def test_fixed_constraints(self):
        self.assertInvalid(tw_mode='fixed', tw_fixed_ids=[])
        self.assertInvalid(tw_mode='fixed', tw_fixed_ids=[self.fixed('weekly', 9)])
        self.assertInvalid(tw_mode='fixed', tw_fixed_ids=[self.fixed('monthly', 9, day_of_month=32)])
        self.assertInvalid(tw_mode='fixed', tw_fixed_ids=[self.fixed('daily', 24)])
        self.create_cron(**self.daily_report_values())

    def test_interval_longer_than_window_warns(self):
        with self.mock_now(self.utc('2026-09-28 06:00')):
            cron = self.create_cron(tw_mode='windows', tw_window_ids=[
                self.window(8, 8.5, every=2, unit='hours'),
            ])
            self.assertTrue(cron.tw_window_ids.interval_exceeds_window)
            self.assertIn('longer than the window', cron.tw_preview)
        self.assertEqual(self.local(cron.nextcall), '2026-09-28 08:00')
        self.assertEqual(self.local(cron._compute_next_call(cron.nextcall)), '2026-09-29 08:00')

    def test_preview(self):
        with self.mock_now(self.utc('2026-09-28 17:50')):
            cron = self.create_cron(**self.sync_ads_values())
            lines = cron.tw_preview.splitlines()
        self.assertEqual(len(lines), 10)
        self.assertIn('2026-09-28 17:55', lines[0])
        self.assertIn('UTC+01:00', lines[0])
        self.assertIn('2026-09-28 18:00', lines[1])
        self.assertIn('2026-09-28 18:30', lines[2])
        self.assertIn('2026-09-28 22:00', lines[-1])

    # ------------------------------------------------------------------
    # Safety net and idempotency
    # ------------------------------------------------------------------

    def test_skip_reason_windows(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(**self.sync_ads_values())
        # The 09:00 slot was planned before the schedule was set, at 09:00.
        self.assertIn('already served', cron._tw_skip_reason(self.utc('2026-09-28 09:02')))
        self.assertIsNone(cron._tw_skip_reason(self.utc('2026-09-28 10:06')))
        self.assertIn('outside', cron._tw_skip_reason(self.utc('2026-09-28 23:30')))
        cron.tw_served_until = self.utc('2026-09-28 10:05')
        self.assertIn('already served', cron._tw_skip_reason(self.utc('2026-09-28 10:07')))
        self.assertIsNone(cron._tw_skip_reason(self.utc('2026-09-28 10:10')))

    def test_skip_reason_fixed(self):
        with self.mock_now(self.utc('2026-09-28 06:00')):
            cron = self.create_cron(**self.daily_report_values())
        self.assertIsNone(cron._tw_skip_reason(self.utc('2026-09-28 07:31')))
        cron.tw_served_until = self.utc('2026-09-28 07:30')
        self.assertIn('already served', cron._tw_skip_reason(self.utc('2026-09-28 08:00')))
        self.assertIsNone(cron._tw_skip_reason(self.utc('2026-09-28 09:05')))

    def test_scheduler_skips_pause_window(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(**self.sync_ads_values())
        cron.nextcall = self.utc('2026-09-28 23:20')  # moved elsewhere, into the pause window
        self.assertEqual(self.run_scheduler(cron, self.utc('2026-09-28 23:30')), 0)
        self.assertEqual(self.local(cron.nextcall), '2026-09-29 08:00')
        self.assertFalse(cron.lastcall, "a skipped execution leaves lastcall alone")

    def test_scheduler_runs_and_reschedules(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(**self.sync_ads_values())
        cron.nextcall = self.utc('2026-09-28 10:05')
        now = self.utc('2026-09-28 10:05') + timedelta(seconds=30)
        self.assertEqual(self.run_scheduler(cron, now), 1)
        self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:10')
        self.assertTrue(cron.lastcall)
        self.assertEqual(cron.tw_served_until, self.utc('2026-09-28 10:05'), "the slot, not the clock")

    def test_scheduler_runs_last_evening_slot(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(**self.sync_ads_values())
        cron.nextcall = self.utc('2026-09-28 22:30')
        now = self.utc('2026-09-28 22:30') + timedelta(seconds=30)
        self.assertEqual(self.run_scheduler(cron, now), 1)
        self.assertEqual(self.local(cron.nextcall), '2026-09-29 08:00')

    def test_scheduler_runs_once_per_slot(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(**self.sync_ads_values())
        lastcall = self.utc('2026-09-28 10:05') + timedelta(seconds=20)
        cron.write({
            'nextcall': self.utc('2026-09-28 10:10'),
            'lastcall': lastcall,
            'tw_served_until': self.utc('2026-09-28 10:05'),
        })
        # A trigger wakes the job up again within the 10:05 slot.
        self.assertEqual(self.run_scheduler(cron, self.utc('2026-09-28 10:07')), 0)
        self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:10')
        self.assertEqual(cron.lastcall, lastcall)

    def test_scheduler_fixed_times(self):
        with self.mock_now(self.utc('2026-09-28 06:00')):
            cron = self.create_cron(**self.daily_report_values())
        self.assertEqual(self.local(cron.nextcall), '2026-09-28 07:30')
        now = self.utc('2026-09-28 07:30') + timedelta(seconds=40)
        self.assertEqual(self.run_scheduler(cron, now), 1)
        self.assertEqual(self.local(cron.nextcall), '2026-09-28 09:00')

    def test_scheduler_native_cron(self):
        cron = self.create_cron(nextcall=self.utc('2026-09-28 10:00'))
        self.assertEqual(self.run_scheduler(cron, self.utc('2026-09-28 23:30')), 1)
        self.assertGreater(cron.nextcall, self.utc('2026-09-28 10:00'), "Odoo moved it forward itself")
        self.assertFalse(cron.tw_served_until)

    def test_manual_run_ignores_the_schedule(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(**self.sync_ads_values())
        cron.nextcall = self.utc('2026-09-29 08:00')
        self.assertEqual(
            self.run_manually(cron, self.utc('2026-09-28 23:30')), 1,
            "a manual run executes even in a pause window",
        )
        self.assertEqual(self.local(cron.nextcall), '2026-09-29 08:00', "the planned slot is kept")

    def test_manual_run_uses_up_the_pending_slot(self):
        with self.mock_now(self.utc('2026-09-28 09:00')):
            cron = self.create_cron(**self.sync_ads_values())
        cron.nextcall = self.utc('2026-09-28 10:05')
        self.assertEqual(self.run_manually(cron, self.utc('2026-09-28 10:06')), 1)
        self.assertEqual(cron.tw_served_until, self.utc('2026-09-28 10:05'))
        self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:10', "the worker will not run it again")
