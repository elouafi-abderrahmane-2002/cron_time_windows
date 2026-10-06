from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CronTwWizard(models.TransientModel):
    """Apply one set of time windows to several scheduled actions at once.

    The schedule comes from a template, from another scheduled action, or is defined in
    the wizard itself. Choosing a source loads its lines into the wizard, where they can
    be adjusted before being applied.
    """

    _name = 'cron.tw.wizard'
    _inherit = 'cron.tw.settings.mixin'
    _description = 'Apply Time Windows to Scheduled Actions'

    cron_ids = fields.Many2many(
        'ir.cron',
        string='Scheduled Actions',
        required=True,
        default=lambda self: self._default_cron_ids(),
    )
    source = fields.Selection(
        [
            ('manual', 'Define them here'),
            ('template', 'Time window template'),
            ('cron', 'Another scheduled action'),
        ],
        string='Source',
        required=True,
        default='manual',
    )
    template_id = fields.Many2one('cron.tw.template', string='Template')
    reference_cron_id = fields.Many2one(
        'ir.cron',
        string='Reference Scheduled Action',
        domain="[('tw_mode', '!=', 'native')]",
    )
    tw_mode = fields.Selection(default='windows')
    tw_window_ids = fields.One2many('cron.tw.wizard.window', 'wizard_id', string='Time Windows')
    tw_fixed_ids = fields.One2many('cron.tw.wizard.fixed', 'wizard_id', string='Fixed Times')

    @api.model
    def _default_cron_ids(self):
        if self.env.context.get('active_model') != 'ir.cron':
            return self.env['ir.cron']
        return self.env['ir.cron'].browse(self.env.context.get('active_ids') or [])

    @api.onchange('source', 'template_id', 'reference_cron_id')
    def _onchange_tw_source(self):
        origin = self._tw_source()
        if origin:
            self.update(origin._tw_copy_vals())

    def _tw_source(self) -> models.BaseModel:
        """Return the template or scheduled action to copy, empty for a manual schedule."""
        self.ensure_one()
        if self.source == 'template':
            return self.template_id
        if self.source == 'cron':
            return self.reference_cron_id
        return self.env['cron.tw.template']

    def _tw_after_lines_change(self) -> None:
        """Wizard lines are validated when the schedule is applied, not while edited."""

    def _tw_apply_vals(self) -> dict:
        """Return the values written on each selected scheduled action."""
        self.ensure_one()
        if self.tw_mode == 'native':
            # Back to Odoo's scheduling: the time windows and fixed times are kept.
            return {'tw_mode': 'native'}
        return self._tw_copy_vals()

    def action_apply(self):
        """Write the schedule on every selected scheduled action."""
        self.ensure_one()
        if not self.cron_ids:
            raise UserError(_("Select at least one scheduled action."))
        origin = self._tw_source()
        if self.source != 'manual' and not origin:
            raise UserError(_(
                "Choose the template or the scheduled action to copy the time windows from."
            ))
        if origin and self.tw_mode != 'native' and not (self.tw_window_ids or self.tw_fixed_ids):
            # The form loads the source through the onchange; RPC callers may skip it.
            self.write(origin._tw_copy_vals())
        self._tw_check_config()
        for cron in self.cron_ids:
            cron.write(self._tw_apply_vals())
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _(
                    "Time windows applied to %(count)s scheduled action(s).",
                    count=len(self.cron_ids),
                ),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class CronTwWizardWindow(models.TransientModel):
    """A time window being defined in the wizard."""

    _name = 'cron.tw.wizard.window'
    _inherit = 'cron.tw.window.mixin'
    _description = 'Cron Time Windows Wizard Window'
    _tw_owner_field = 'wizard_id'

    wizard_id = fields.Many2one('cron.tw.wizard', required=True, index=True, ondelete='cascade')


class CronTwWizardFixed(models.TransientModel):
    """A fixed execution time being defined in the wizard."""

    _name = 'cron.tw.wizard.fixed'
    _inherit = 'cron.tw.fixed.mixin'
    _description = 'Cron Time Windows Wizard Fixed Time'
    _tw_owner_field = 'wizard_id'

    wizard_id = fields.Many2one('cron.tw.wizard', required=True, index=True, ondelete='cascade')
