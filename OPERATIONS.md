# Accounts, patient access and operations

## Staff sign-in

Sign in as the initial administrator, then open **Staff management**. Create a
named staff account, select its role and privately give that person a temporary
password (12–256 characters). Their first sign-in requires a different password.
The same page can reset passwords, disable access and change staff roles; these
actions revoke sessions. Self-demotion/disable and removing the last administrator
are blocked. There are no shared/default credentials. Trusted-host recovery is
documented in STAFF_ACCESS.md.

## Patient portal

An administrator or front desk user selects a patient in **Patients**, opens the
portal invitation panel and generates a code. Verify identity before sharing it
privately. Codes expire after 48 hours, are single-use, and replacement invitations
invalidate older ones. Patients use **Activate patient invitation** on the sign-in
screen, choose their own username/password, then sign in normally.

Patients see only their linked appointments. They can request routine bookings,
cancel unstarted bookings, reschedule with explicit waitlist consent, confirm
planned attendance and manage reminder consent. Attendance confirmation does not
check them in or clear billing. Staff see that confirmation under Appointments.
Patient accounts cannot access clinical workspaces or assign their own urgency.
Staff-created invitations establish the patient link; mailbox verification is a
separate feature, not evidence of clinical identity.

Email verification requires RESEND_API_KEY, REMINDER_FROM_EMAIL, and the separate
MEDAGENT_V2_EMAIL_VERIFICATION_ENABLED=true flag. The patient explicitly requests
a code. Codes expire after 15 minutes, have five attempts, are bound to the current
recorded address, and requests are limited to once per minute. Changing the email
clears verification and unstarted reminder consent. No address is silently fixed.
Provider acceptance is not inbox delivery. Tests use fake senders. Live delivery
must be configured and tested separately; see INTEGRATION.md for reminders.

## Availability and consultation timing

Configure capacity first in Doctor sessions. Then set same-day working hours,
dated availability/leave and breaks. Leave excludes new allocations and prevents
new consultation starts; existing reservations stay visible for staff to contact
and reschedule. Breaks and working hours constrain consultation starts, not daily
booking quotas. This is date-based booking, not a guaranteed appointment time.
Overnight shifts and automatic reassignment are not implemented.

Physicians/admins press Start consultation. A doctor and visit can have at most
one active consultation segment. Selecting the next care step ends the segment.
Visits returning from diagnostics can start another segment. No fixed duration
or forced interruption is imposed. After three completed segments, the care screen
uses the median of the last 30 as an approximate queue-workload estimate. It does
not account for every interruption, remaining consultation time or break; it is
not a promised start time. Staff can route care without timing it; such care is
excluded from timing estimates.

## Backup and recovery

Administrators can download a consistent SQLite snapshot from Evaluation & backup.
The file contains patient records, password hashes and audit history: store it
privately. Active login sessions, invitation codes and verification codes are
excluded. Backups are manual, not scheduled or externally retained.

Trusted-host commands (destinations must not exist):

```sh
python -m hospital.manage --database medagent_v2.db backup private-backup.db
python -m hospital.manage --database recovered.db restore private-backup.db
```

Restore never overwrites a live database. Stop the app/worker, validate the new
file, set MEDAGENT_V2_DB_PATH to its absolute path, and restart both against that
same file. Backups preserve password hashes, so existing account passwords work;
everyone must sign in again. Protect the backup and its host access.

The current free preview has ephemeral storage. Restarts/redeployments can lose
accounts and patient records. This update does not purchase persistent hosting,
recover lost records, or create automatic backups. Use synthetic data. For durable
operation, provision persistent storage (or migrate to a server database), run the
reminder worker on an always-on host against the same database, and test recovery.
Do not put a second SQLite worker on an unrelated service filesystem.

## Synthetic evaluation

Evaluation & backup compares FCFS and urgency-with-aging using the same generated
arrivals and variable consultation lengths. It reports mean and 95th percentile
waiting time by urgency. The aging policy is experimental; it does not change the
live agent rules or establish clinical effectiveness. Runs use no live records.

```sh
python -m hospital.manage --database unused.db evaluate --seed 42 --count 120 --doctors 2
```

This evaluation command does not open/create the supplied database. The live
system remains a rule-based, event-driven workflow system, with durable decisions
and human clinical oversight. It is not autonomous diagnosis or validated triage.
