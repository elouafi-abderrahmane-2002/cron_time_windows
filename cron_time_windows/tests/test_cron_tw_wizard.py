from odoo.exceptions import ValidationError
from odoo.tests import Form, tagged

from .common import CronTwCase


@tagged('post_install', '-at_install')
class TestCronTwWizard(CronTwCase):
    """Applying one set of time windows to several scheduled actions at once."""

    def setUp(self):
        super().setUp()
        clock = self.mock_now(self.utc('2026-09-28 10:02'))
        clock.start()
        self.addCleanup(clock.stop)
        self.crons = self.create_cron(name='Job A') | self.create_cron(name='Job B')
        self.Wizard = self.env['cron.tw.wizard'].with_context(
            active_model='ir.cron', active_ids=self.crons.ids,
        )

    def _sync_ads_template(self):
        return self.env['cron.tw.template'].create({'name': 'Sync ads', **self.sync_ads_values()})

    def test_selected_crons_are_the_default(self):
        wizard = self.Wizard.create({'tw_window_ids': [self.window(8, 18, every=5)]})
        self.assertEqual(wizard.cron_ids, self.crons)

    def test_apply_template(self):
        template = self._sync_ads_template()
        for _attempt in range(2):
            self.Wizard.create({'source': 'template', 'template_id': template.id}).action_apply()
        for cron in self.crons:
            self.assertEqual(cron.tw_mode, 'windows')
            self.assertEqual(cron.tw_window_ids.mapped('hour_from'), [8.0, 18.0, 23.0], "lines replaced, not added")
            self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:05')

    def test_apply_reference_cron(self):
        reference = self.create_cron(name='Daily report', **self.daily_report_values())
        self.Wizard.create({'source': 'cron', 'reference_cron_id': reference.id}).action_apply()
        for cron in self.crons:
            self.assertEqual(cron.tw_mode, 'fixed')
            self.assertEqual(len(cron.tw_fixed_ids), 2)
            self.assertEqual(self.local(cron.nextcall), '2026-09-29 07:30')

    def test_apply_manual_schedule(self):
        self.Wizard.create({
            'tw_mode': 'windows',
            'tw_outside_action': 'default_interval',
            'tw_window_ids': [self.window(8, 12, every=15)],
        }).action_apply()
        for cron in self.crons:
            self.assertEqual(cron.tw_outside_action, 'default_interval')
            self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:15')

    def test_apply_native_keeps_the_lines(self):
        self.Wizard.create({'tw_window_ids': [self.window(8, 18, every=5)]}).action_apply()
        self.Wizard.create({'tw_mode': 'native'}).action_apply()
        for cron in self.crons:
            self.assertEqual(cron.tw_mode, 'native')
            self.assertEqual(len(cron.tw_window_ids), 1)

    def test_native_wins_over_the_source(self):
        template = self._sync_ads_template()
        self.Wizard.create({
            'source': 'template', 'template_id': template.id, 'tw_mode': 'native',
        }).action_apply()
        for cron in self.crons:
            self.assertEqual(cron.tw_mode, 'native')
            self.assertFalse(cron.tw_window_ids)

    def test_invalid_schedule_is_rejected(self):
        wizard = self.Wizard.create({'tw_window_ids': [
            self.window(8, 18, every=5), self.window(17, 20, every=10),
        ]})
        with self.assertRaises(ValidationError):
            wizard.action_apply()
        self.assertEqual(set(self.crons.mapped('tw_mode')), {'native'})

    def test_template_validation(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['cron.tw.template'].create({
                'name': 'Overlapping',
                'tw_window_ids': [self.window(8, 18, every=5), self.window(17, 20, every=10)],
            })

    def test_form_loads_the_source(self):
        template = self._sync_ads_template()
        form = Form(self.Wizard)
        form.source = 'template'
        form.template_id = template
        self.assertEqual(len(form.tw_window_ids), 3)
        with form.tw_window_ids.edit(0) as window:
            window.interval_number = 10
        form.save().action_apply()
        for cron in self.crons:
            self.assertEqual(cron.tw_window_ids[0].interval_number, 10)
            self.assertEqual(self.local(cron.nextcall), '2026-09-28 10:10')
