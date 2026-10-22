# Appointment email reminders

The v2 Reminder Agent sends date-only reminders for tomorrow's confirmed routine
appointments, at or after 09:00 Africa/Lagos. Patients must opt in and provide a
valid email. Emergency, cancelled, completed, unpaid and waitlisted appointments
are excluded. No fixed consultation duration is promised.

## Brevo and GitHub Actions setup

Delivery is disabled by default. No paid worker is required. Create a Brevo account,
verify a sender and create an API key with transactional sending access. Use the
HTTPS API key, not an SMTP key. Complete any account review Brevo requests.

Under GitHub repository Settings → Secrets and variables → Actions, add:

| Type | Name | Value |
| --- | --- | --- |
| Secret | MEDAGENT_DATABASE_URL | Production Neon PostgreSQL connection string |
| Secret | BREVO_API_KEY | Brevo API key |
| Variable | REMINDER_FROM_EMAIL | Verified sender email address |
| Variable | MEDAGENT_REMINDERS_ENABLED | Leave false until ready, then true |

Never commit credentials or paste them into chat. The workflow runs hourly at
minute 17 and can also be run manually from Actions → Appointment reminders.
It connects directly to the persistent database, so Render may remain asleep.
It requires the existing v2 schema; it does not create or reset patient records.
GitHub scheduled runs can be delayed or missed and public inactive repositories
may have schedules disabled. This is a portfolio service, not a clinical alert system.
Check current provider quotas and GitHub billing settings before enabling it.

For the app's optional patient email verification, configure Render privately with
EMAIL_PROVIDER=brevo, BREVO_API_KEY, REMINDER_FROM_EMAIL, and
MEDAGENT_V2_EMAIL_VERIFICATION_ENABLED=true. Enable this only after the sender test.
The scheduled worker does not require Render's reminder flag to be enabled.

Before activation, review opted-in upcoming appointments. Use only synthetic demo
data and authorized recipients. Test delivery to your own address first and check
Brevo's transactional logs and your inbox. Provider acceptance alone does not prove
inbox delivery. No live messages are sent by the test suite.

## Delivery and duplicate protection

A unique appointment/date/revision claim is committed before sending. Repeated or
concurrent passes cannot send the same claimed reminder again while its record
persists. Rescheduling creates a new revision. Brevo uses this database protection;
Resend also receives an idempotency key. ACCEPTED means provider acceptance.
Errors become REVIEW_REQUIRED and interruption can leave SENDING. Neither is
retried automatically because a timeout can occur after the provider accepts mail.
Check the provider dashboard before recovery; do not simply delete claims.
A cancellation after a claim can race with delivery. A crash can cause a missed
reminder rather than duplicate mail. Back up the persistent database.

## Existing Resend / legacy deployments

EMAIL_PROVIDER defaults to resend for compatibility. Resend requires RESEND_API_KEY
and REMINDER_FROM_EMAIL. The v2 opt-in activation flag is
MEDAGENT_V2_REMINDERS_ENABLED=true. Legacy app.py reminders use REMINDERS_ENABLED
and its SQLite worker; do not point the new v2 job at the legacy schema.
REMINDER_TIMEZONE defaults to Africa/Lagos for either provider.

## Verification

Run `python -m pytest -q`. Provider tests mock HTTPS; workflow tests exercise
claiming, concurrent dispatch, consent, rescheduling and ambiguous failures.

References:
- https://developers.brevo.com/reference/send-transac-email
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
