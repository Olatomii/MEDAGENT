# Patient and agent foundation — integrated development version

See `INTEGRATION.md` for the billing, vitals, ward and reminder integration,
worker commands, migration behavior and delivery limitations.

This is a runnable next-version application, not a replacement deployment.
Run `python -m streamlit run app_v2.py` from the repository directory.
The original `app.py`, its database, and the current Render preview keep their
existing behavior. The new app uses `MEDAGENT_V2_DB_PATH` (default `medagent_v2.db`).
Do not point it at the original database. Use synthetic data while developing.

## Try the complete journey

1. In Patients, create a record and retain its permanent patient ID.
2. In Doctor sessions, configure a doctor's booking capacity for today.
3. In Appointments, select that patient ID and book the matching department.
4. Record routine billing clearance with a staff reference, then check in the
   confirmed booking. Emergencies bypass billing. Only check-in creates a visit.
5. In Care workspace, record vital signs to complete assessment, then consultation, diagnostics,
   pharmacy, admission, completion, or transfer as permitted by the state rules.
6. Open Patients again to review the patient's linked booking and visit history.
7. Fill a session and create another booking to produce a waitlist entry. Cancel
   an unstarted confirmed booking: the waitlist agent allocates the released place.
   Agent decisions shows the triggering event and selection reason. Configure
   ward capacity under Wards before admission; discharge releases the place.

No fixed consultation length is imposed. Session capacity is a booking quota,
not a prediction of the number of consultations that will actually finish.
Completed routine visits continue to consume their original booking allocation.
Emergency session capacity instead represents concurrent places across dates;
admission, completion or transfer-required routing releases an emergency place.
Live routine clinician occupancy, leave, breaks and reassignment are future work.

## Data and agent boundaries

- `patients`: permanent identity. Names are not unique; never automatically merge
  two people by name, email or demographics. Age is recorded age, not date of birth.
- `appointments`: date, department, assigned doctor and reservation status.
- `visits`: one actual encounter per checked-in appointment, with state/version.
- `events`: durable transactional event history and pending processing state.
- `decisions`: agent, triggering event, affected record and reason.
- `hospital/service.py`: validates commands and writes events with state changes.
- `hospital/agents.py`: appointment, waitlist and care-coordination handlers.

The dispatcher is synchronous, with durable recovery. It runs after service
commands and on page interactions. A separate unattended worker is available but
is **not** deployed; see `INTEGRATION.md` for its command and persistence requirements.
For a one-shot recovery run:

```sh
python -m hospital.manage --database medagent_v2.db dispatch
```

One SQLite write transaction serializes capacity allocation. Each event's effects,
decisions and completion marker commit together; a handler failure rolls them all
back and leaves the event pending. Reprocessing an already completed event is a
no-op. The dispatcher stops at a failed event for investigation. These guarantees
cover database effects, not external messages or email delivery.

Care transitions require the expected record version and a permitted source state.
Completed and transferred visits cannot return to a care queue. UI action keys
include that version so an old displayed action is not reused after a transition.
Urgency is assigned by staff or the original simulation's vital-sign rules, not a validated clinical model. Emergency intake
cannot be scheduled for a future date or silently enter a routine waitlist.

## Legacy import

Import a **separate immutable snapshot**, never a changing production database:

```sh
python -m hospital.manage --database medagent_v2.db import-legacy /path/to/legacy-snapshot.db
```

The source is opened read-only. Each source row gets its own patient identity;
the original system has no reliable identifier that supports automatic merging.
All source fields are retained as JSON in `legacy_records`, with a source mapping.
Original source timestamps in that JSON are authoritative; new record timestamps
reflect import time. Repeating the same snapshot/path is a no-op. A changed row
at an already imported source key aborts the import. Unknown doctors, statuses or
locations also abort atomically; nothing is silently dropped.

Unassessed legacy records at Nurses Station become confirmed appointments with
no visit because original registration did not prove arrival. Staff must review
them before checking in. In-progress care and terminal visits retain their mapped
state. Past records are not automatically rolled forward. Doctor sessions must be
configured after import; imported allocations are counted against those sessions.
No live database has been imported during this milestone.

## Verification and remaining milestones

`python -m pytest -q` tests both versions. Foundation tests cover separate check-in,
repeat actions, terminal/stale transitions, simultaneous capacity claims,
priority promotion, recovery after a handler failure, and read-only legacy import.

Before replacing the existing app, remaining work includes:

- A patient portal and email ownership verification.
- Clinician working hours/leave, active consultation tracking and queue estimates.
- Authentication, server-enforced roles and audit attribution before real patient use.
- Persistent hosting, backup/restore checks and deployment of the managed worker.
- Synthetic workload evaluation against a first-come-first-served baseline.

Existing group history remains in Git; this development milestone adds new modules
without relabeling or removing earlier contributions.
