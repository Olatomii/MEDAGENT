# Appointment email reminders

Front Desk now offers email reminders for future routine bookings. The patient must
opt in and supply a valid email address. Existing bookings remain opted out.
Waitlist entries retain the preference but do not trigger emails. Completed,
absent, admitted, diverted, emergency, and already-progressed visits are excluded.

The Reminder Agent checks every minute while the Streamlit process is alive,
starting with the first page load. At or after 09:00 Africa/Lagos it sends one
date-only reminder for tomorrow's confirmed appointment. The System Target Date
does not advance this clock. Rollover does not create a new reminder commitment:
the original reminder date must still match the appointment date. A missed
previous-day window is not sent late on the appointment day.

## Activation

Sending is disabled by default. The implementation uses Resend's HTTPS API.
Configure these environment variables privately in the hosting dashboard:

- `RESEND_API_KEY`: a Resend sending API key; never commit this value.
- `REMINDER_FROM_EMAIL`: a sender address authorized by your Resend account/domain.
- `REMINDERS_ENABLED`: `true` only when ready for delivery.
- `REMINDER_TIMEZONE`: optional, defaults to `Africa/Lagos`.
- `MEDAGENT_DB_PATH`: persistent SQLite path for reliable scheduled use.

Free Render web services sleep and their local filesystem is ephemeral. A thread
cannot send while the service sleeps, and deleted database history cannot prevent
duplicates. For dependable unattended delivery use an always-running instance
with a persistent disk and one instance, or migrate shared state to a persistent
database with a separately scheduled worker. No paid resources were provisioned.
For demonstration only, open the app during the reminder window to start checks.

## Delivery and duplicate protection

A unique appointment/date claim is committed before sending, with a matching
Resend idempotency key. Repeated checks, browser reruns, and concurrent workers
do not send the same claimed appointment/date again while its record persists.
`ACCEPTED` means the provider accepted the request, not inbox delivery. Provider
errors become `REVIEW_REQUIRED`; process interruption may leave `SENDING`.
These are deliberately not retried automatically, because a timeout can happen
after acceptance. Verify delivery in the provider dashboard before recovery.
Do not delete the claim merely to retry. A crash can therefore cause a missed
reminder rather than a duplicate. A cancellation committed after a send was
claimed can race with delivery; no guarantee is made about recalling that email.

System Telemetry shows status counts without email addresses. Reset deletes
reminder history along with patient records. Existing public prototype access
controls are unchanged; this is not a production patient-notification service.

## Verification

Run `python -m pytest -q`. Tests use temporary databases and fake senders. No real
patient emails are sent. Live delivery still requires provider configuration and
a controlled end-to-end test to an authorized recipient.

Provider references:
- https://resend.com/docs/api-reference/emails/send-email
- https://resend.com/docs/dashboard/emails/idempotency-keys
- https://render.com/docs/free
