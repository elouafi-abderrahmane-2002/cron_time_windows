# Cron Time Windows - Odoo 18.0

Business hours and fixed times for Odoo scheduled actions (`ir.cron`).

- **Time windows**: run a cron every 5 minutes during office hours, every 30 minutes
  in the evening, never at night. Each window has its own frequency, weekdays and
  can cross midnight.
- **Fixed times**: run a cron every day at 07:30, every Monday at 09:00, or on the
  last day of the month.
- Timezone aware (daylight saving included), preview of the next executions,
  reusable templates and a wizard to apply a schedule to many crons at once.

Every cron stays on Odoo's native scheduling until you change it.

## Installation

Copy the `cron_time_windows` folder into your addons path, update the apps list and
install **Cron Time Windows**. It depends on `base` only and works on Community and
Enterprise.

## Usage

Developer mode, then *Settings > Technical > Automation > Scheduled Actions*: open a
cron and use its **Time Windows** tab. See `cron_time_windows/README.rst`.

## Branches

One branch per Odoo version: `16.0`, `17.0`, `18.0`, `19.0`, `20.0`.

## License

LGPL-3. Author: [WebThreenity](https://webthreenity.netlify.app), Odoo implementation and development.
Questions or custom work: elouafiabderrahmane2002@gmail.com.
