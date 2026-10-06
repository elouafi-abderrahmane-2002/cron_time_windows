{
    'name': 'Cron Time Windows - Business Hours & Fixed Times Scheduler',
    'version': '17.0.1.0.0',
    'summary': 'Run each scheduled action only within its time windows, with its own '
               'frequency per window, or at fixed daily, weekly and monthly times',
    'category': 'Extra Tools',
    'author': 'WebThreenity',
    'website': 'https://webthreenity.netlify.app',
    'support': 'elouafiabderrahmane2002@gmail.com',
    'license': 'LGPL-3',
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
        'views/ir_cron_views.xml',
        'views/cron_tw_template_views.xml',
        'wizard/cron_tw_wizard_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
}
