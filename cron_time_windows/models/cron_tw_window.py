from odoo import fields, models


class CronTwWindow(models.Model):
    """A time window of a scheduled action in ``windows`` mode."""

    _name = 'cron.tw.window'
    _inherit = 'cron.tw.window.mixin'
    _description = 'Cron Time Window'
    _tw_owner_field = 'cron_id'

    cron_id = fields.Many2one(
        'ir.cron',
        string='Scheduled Action',
        required=True,
        index=True,
        ondelete='cascade',
    )


class CronTwFixed(models.Model):
    """A fixed execution time of a scheduled action in ``fixed`` mode."""

    _name = 'cron.tw.fixed'
    _inherit = 'cron.tw.fixed.mixin'
    _description = 'Cron Fixed Time'
    _tw_owner_field = 'cron_id'

    cron_id = fields.Many2one(
        'ir.cron',
        string='Scheduled Action',
        required=True,
        index=True,
        ondelete='cascade',
    )
