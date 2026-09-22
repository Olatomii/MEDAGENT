# Patient experience and operational follow-up

This update targets the existing free preview only. It does not include PR #4,
purchase hosting, enable real email, or provide permanent storage. Preview data
can still disappear on redeployment/restart. Download a private backup before a
hosting change if existing records need to be retained.

## Registration and identity review

New patients can use **New patient? Create an account** on the sign-in screen,
after the hospital's initial administrator is configured. Patient IDs are generated
by the application. Registration requires an email but does not prove ownership;
verification still needs the configured email provider. Staff accounts cannot be
created by this form. Internet-facing abuse protection remains deployment work.

Matching normalized name plus age, or matching email, flags possible duplicates.
These rules are intentionally conservative and are not identity proof: shared
family email addresses and same-name people are legitimate. Spelling variants,
changed ages or different emails may escape detection. Existing records are never
linked, disclosed or merged by public registration. A matching registration gets
a separate patient ID/account with no access to existing history. Routine portal
booking is held until staff review the matches in Patients → Identity reviews.

If staff verify different people, record the check and keep the records separate.
If they are the same person, leave the review unresolved: an administrator disables
the new account and gives verified access to the original through invitation or
password reset. No automated record merging or history deletion is provided.
Staff registration also blocks possible matches unless the operator explicitly
confirms a different person. Patient search now includes email as well as name/ID.

## Doctor reassignment and portal updates

In Appointments, **Change assigned doctor** lists available same-department doctors
for an unstarted, confirmed routine appointment on today or a future date.
Reassignment rechecks capacity in one write transaction, rejects stale revisions,
retains date and billing, clears attendance confirmation, records the old/new doctor
and staff reason, then evaluates released capacity. The patient portal presents a
new update without exposing the staff reason or other patients' records.

Waitlisted bookings remain allocated by the waitlist agent. Emergency and started
visits need clinical coordination; this feature cannot reassign those visits.
Working hours/breaks retain the existing date-quota semantics. A new revision can
qualify for a new day-before reminder if sending is enabled. There is no immediate
email or push notification for a doctor change; staff should contact the patient.

Patients see the latest 50 relevant booking updates, attendance confirmations,
cancellations, waitlist promotions and doctor/date changes. These are scoped by
patient ID in the query. Current booking cards remain authoritative. Updates
refresh on interaction/page reload, not through a push service. Older event-only
messages show the appointment ID and UTC timestamp, not historical clinical notes.

## Overview and agent follow-up

Admin, front desk, nurse and physician roles can access Overview. Front desk sees
appointment counts, session allocations and unavailable-doctor flags only; clinical
elapsed-time and transfer flags are excluded. Patients/pharmacy/ward accounts
cannot access this overview. Existing clinical roles retain their shared clinical
read access, not individual doctor assignment restrictions.

The rules evaluate on dashboard refresh: unavailable assigned doctors, active
unresolved transfers, and elapsed time over 60 minutes in other waiting care states
or 120 minutes in diagnostics. Started consultation segments are excluded from
long-wait flags. These are illustrative operational thresholds, not clinically
validated deadlines; they do not override urgency or make treatment decisions.
A recorded follow-up is attributed to the staff user and remains visible while the
underlying condition persists. Completing a follow-up note alone does not complete
care or confirm a transfer. Clinical state changes produce a fresh review key.

The dashboard displays today's booking status counts and doctor allocations,
plus count/median/maximum elapsed time in current care states for clinical roles.
Elapsed time includes work in progress and is not estimated time to appointment.
Flags and metrics are recomputed while the app is running; this free deployment
has no guaranteed unattended timer or external notification service.

## Mobile interface and validation

Small screens stack columns, make actions full-width, preserve 44px touch targets,
use readable input text and wrap long IDs. Completed patient bookings collapse
by default; current bookings remain open. Native labeled controls and existing
focus styles are retained. Data tables can scroll horizontally.

Tests cover self-registration/login/booking, duplicate review and identity isolation,
atomic reassignment contention, patient-scoped updates, role-restricted alerts and
all staff pages. Synthetic test fixtures explicitly acknowledge their intentionally
repeated identities. No real emails are sent during tests.
