from odoo import api, fields, models

from .cron_tw_mixin import SKIP_OWNER_HOOK


class CronTwTemplate(models.Model):
    """A named, reusable set of time windows, applied to scheduled actions with the wizard."""

    _name = 'cron.tw.template'
    _inherit = 'cron.tw.settings.mixin'
    _description = 'Cron Time Windows Template'
    _order = 'name, id'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    tw_mode = fields.Selection(default='windows')
    tw_window_ids = fields.One2many(
        'cron.tw.template.window', 'template_id', string='Time Windows', copy=True,
    )
    tw_fixed_ids = fields.One2many(
        'cron.tw.template.fixed', 'template_id', string='Fixed Times', copy=True,
    )
    note = fields.Text(string='Notes')

    @api.constrains('tw_mode', 'tw_outside_action', 'tw_window_ids', 'tw_fixed_ids')
    def _check_tw_schedule(self):
        self._tw_check_config()

    @api.model_create_multi
    def create(self, vals_list):
        templates = super(
            CronTwTemplate, self.with_context(**{SKIP_OWNER_HOOK: True}),
        ).create(vals_list)
        return templates.with_env(self.env)

    def write(self, vals):
        return super(
            CronTwTemplate, self.with_context(**{SKIP_OWNER_HOOK: True}),
        ).write(vals)


class CronTwTemplateWindow(models.Model):
    """A time window of a template."""

    _name = 'cron.tw.template.window'
    _inherit = 'cron.tw.window.mixin'
    _description = 'Cron Time Windows Template Window'
    _tw_owner_field = 'template_id'

    template_id = fields.Many2one(
        'cron.tw.template',
        string='Template',
        required=True,
        index=True,
        ondelete='cascade',
    )


class CronTwTemplateFixed(models.Model):
    """A fixed execution time of a template."""

    _name = 'cron.tw.template.fixed'
    _inherit = 'cron.tw.fixed.mixin'
    _description = 'Cron Time Windows Template Fixed Time'
    _tw_owner_field = 'template_id'

    template_id = fields.Many2one(
        'cron.tw.template',
        string='Template',
        required=True,
        index=True,
        ondelete='cascade',
    )
