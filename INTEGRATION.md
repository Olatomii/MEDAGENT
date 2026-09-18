# Integrated care and reminders in v2

Start `python -m streamlit run app_v2.py` with a separate `MEDAGENT_V2_DB_PATH`.
No current Render deployment, live database or email settings have been changed.
Use synthetic patients: authentication and enforced roles are still a later milestone.

## Care and billing

Routine bookings reserve a place with billing PENDING. Staff record clearance and
a reference before check-in. This records a billing decision; it does not charge
money or validate a payment. Emergency intake is EXEMPT. Existing first-release
routine v2 records migrate to pending, but already-started visits remain usable.

Structured vitals include all five measurements, timestamp and flag, and are
visible in patient history and care screens. The original prototype thresholds
are retained for simulation only, not presented as validated clinical triage.
Flagged readings reserve an emergency place if staffed capacity exists. Otherwise
the visit remains recorded as TRANSFER_REQUIRED, requiring staff action; this does
not claim that an external transfer took place. Appointment history stays intact;
visit urgency and the assigned doctor reflect the escalation separately.

Routine sessions are daily booking quotas. Emergency sessions are concurrent
places: unstarted ED reservations and unresolved visits assigned to an ED doctor
count across dates. The doctor must have a configured session for today.
Completion, ward admission or transfer-required routing releases an emergency
place. Routine completion does not create another bookable appointment.

Admissions reserve configured ward places atomically. Generic transitions cannot
bypass this check. Full wards leave the patient in consultation. Discharge or
transfer ends occupancy, preserving historical ward assignment. Existing v2
admissions without a ward appear for explicit reconciliation in Wards. Legacy
imports map known wards and retain source billing clearance; imported occupancy
may exceed the initial zero configuration and must be reviewed before admissions.

Diagnostics requires a result note before return to consultation, with urgency
unchanged. Pharmacy completion, discharge, vitals and admissions reject repeated
or stale actions. These are staff-controlled workflows, not autonomous diagnosis.

## Reminder worker

Set `RESEND_API_KEY`, `REMINDER_FROM_EMAIL` and the separate flag
`MEDAGENT_V2_REMINDERS_ENABLED=true` on the worker to enable actual sending. The
original `REMINDERS_ENABLED` flag alone never enables v2. The existing Resend HTTPS
adapter is reused; credentials stay outside source code. No real emails were sent
in integration tests, and provider/inbox delivery has not been verified.

One-shot commands:

```sh
python -m hospital.manage --database medagent_v2.db dispatch
python -m hospital.manage --database medagent_v2.db reminders
```

Continuous worker, with a default 60-second interval:

```sh
python -m hospital.manage --database /persistent/path/medagent_v2.db worker
```

The worker and web process must access the same persistent SQLite file on one host.
An unrelated service with its own local filesystem will not share patients. A
supervisor must restart the worker after failures; it is not deployed here. There
is no hidden sending thread in the web app. A sleeping host cannot send reminders.

Only consenting, confirmed, billing-cleared routine appointments are eligible the
day before, after 09:00 in REMINDER_TIMEZONE (default Africa/Lagos). Missed prior-day
windows are not sent late. Appointment/date claims prevent duplicate attempts while
the database persists. ACCEPTED means provider acceptance, not inbox delivery.
REVIEW_REQUIRED or lingering SENDING requires provider verification before manual
recovery. Ambiguous failures are never automatically retried; a crash after claim
can therefore miss a reminder. Changes after a claim can race with sending;
cancellation or withdrawn consent cannot recall an already accepted message.

Email edits revoke reminder consent on unstarted bookings. Staff must confirm
consent for the updated address. Likely domain typos produce suggestions, without
rewriting addresses. Syntax validation does not verify mailbox existence/ownership.

## Appointment rescheduling

Unstarted routine bookings can move to today or a future date, within their
existing department. The operation serializes allocation of the new place and
release of the old one. If the target is full, the original booking remains
unchanged unless staff explicitly select the option to join the new date's waitlist.
The source date's eligible waitlist is then processed through a durable capacity event.

Patient identity, original creation time, billing reference and clearance stay
with the appointment. This assumes transferable recorded billing clearance within
the same department; no financial refund or payment movement is performed.
The rescheduling event retains old/new dates and revision. Waiting priority on
the new date starts at rescheduling time. Emergency, started, cancelled and stale
bookings cannot be moved; duplicate active department/date bookings are rejected.

Day-before reminders follow the current date and consent. The revised booking
gets a new reminder identity, even if moved back to a previously used date; old
send history remains intact. Moving to today clears day-before reminder consent.
No immediate date-change email is sent. Staff must communicate the change; prior
emails cannot be recalled, and an already claimed send can race with rescheduling.

## Validation

Run `python -m pytest -q`. Integration cases cover billing, ED exemption, complete
diagnostics/pharmacy journeys, critical-vitals contention, reusable emergency places,
concurrent ward admissions, stale commands, repeatable migrations, notification
eligibility and duplicate claims, ambiguous provider failures, and consent revocation.
Email tests use fake providers and temporary databases only.
