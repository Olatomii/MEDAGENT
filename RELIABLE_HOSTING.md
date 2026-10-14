# Persistent hosting activation plan

The settings in `deploy/render-persistent-settings.json` are a reviewable proposal,
not an API payload or applied change. Target: existing **medagent-preview** only
(`srv-daitth5g1s2s738o4ho0`), branch `fix/medagent-bugs`. Main production is separate.

## Cost and topology

Proposed: one Render Starter web service ($7/month) plus 1 GB disk ($0.25/month),
approximately $7.25/month before taxes/usage, verified September 19, 2026 against
https://render.com/pricing. Confirm the dashboard total before purchase. No
separate paid worker is needed: two supervised processes share the one disk.
A persistent disk is single-instance and causes brief deployment downtime.
See https://render.com/docs/disks. No purchase has been made by these code changes.

## Preserve existing preview data before any change

1. Sign in as administrator. Download a private backup from Evaluation & backup
   **before upgrading, adding a disk or redeploying**. Those actions can discard
   the free service's current files. Keep the backup outside the service.
2. Verify that the download opens and passes SQLite integrity/foreign-key checks.
   Do not upload patient/account data to Git or paste it into chat.
3. Approve the hosting cost, then upgrade the existing preview to Starter and
   attach the proposed 1 GB disk at `/var/data`.
4. Apply the proposed environment paths and start command. Retain the existing
   private setup token and provider credentials; never commit their values.
5. To preserve the old records, upload the backup using authenticated Render shell/
   SSH access into a private temporary path. During a maintenance window stop the
   web and worker and restore to a NEW filename on the disk, for example:

   `python -m hospital.manage --database /var/data/recovered.db restore /var/data/uploaded-backup.db`

   Point MEDAGENT_V2_DB_PATH at `/var/data/recovered.db` and restart. Never overwrite
   an open database. Delete the temporary upload only after recovery is verified.
   If intentionally starting empty, use the existing private token to create the
   initial admin, then remove the setup token once durable operation is verified.
6. Verify the new deployment, existing patient counts and administrator login,
   then restart it and confirm the same accounts/data remain.
7. Check Evaluation & backup for recent worker/events/backup timestamps and the
   first daily snapshot. Restore that snapshot to a separate temporary database
   and verify counts/login before trusting recovery. Do not test restore over live data.

Changing the database path does not automatically migrate the old database.
The mount guard refuses to run if the expected disk is missing, instead of
silently starting a new ephemeral database. Local development can explicitly use
MEDAGENT_REQUIRE_MOUNT=false. The supervised runtime targets Linux (Render).

## Worker and backups

`python -m hospital.runtime` initializes once then supervises Streamlit and
`hospital.manage ... worker`. If either child exits, it terminates the other and
exits nonzero so the hosting platform can restart the pair. SIGTERM stops both.
A maintenance cycle runs every 60 seconds. Reminder, event and backup failures
are reported independently; failed email delivery does not skip backups.

One native SQLite snapshot is published per UTC date, starting on the first cycle.
Snapshots are integrity-checked, private, atomically published and locked against
concurrent rotation. The latest 14 daily copies are retained; pruning only follows
a successful snapshot. Restarts on the same day reuse that day's copy. At most
roughly one day's changes are recoverable between successful snapshots, longer if
jobs fail. Monitor free disk space: full database copies multiply storage needs.

Backups exclude active sessions/invitation/verification codes but retain patient
records and password hashes. The admin screen shows errors, disabled jobs, and a
warning after a worker heartbeat is older than three minutes. This is an in-app
status view, not an external outage notification system.

**Same-disk backups do not survive deletion/loss of the disk.** Keep private
external copies. Automated off-site retention needs a user-selected destination
and credentials; none is configured here. Host disk snapshots are not a substitute
for these SQLite-native backups. Backup/restore commands never overwrite a file.

## Activate real email only after setup

1. Configure a verified Resend sending domain/address and its API key in private
   host environment settings: RESEND_API_KEY and REMINDER_FROM_EMAIL.
2. Keep reminders disabled until an explicitly authorized test recipient accepts
   a test. The requested test recipient in this project is
   Solomondemilade17@gmail.com; no test was sent by this change.
3. Verify provider acceptance and ask the recipient to confirm inbox/spam arrival.
4. Enable MEDAGENT_V2_REMINDERS_ENABLED=true once approved/configured. Verification
   emails use the separate MEDAGENT_V2_EMAIL_VERIFICATION_ENABLED=true flag.
5. Observe an opted-in, confirmed, billing-cleared synthetic appointment due
   tomorrow after 09:00 Africa/Lagos. Confirm only one claim/send after restarting
   the worker. Remove synthetic records through normal UI actions afterwards.

Existing reminder idempotency/ambiguous-failure protections remain in place.
SENDING/REVIEW_REQUIRED attempts are flagged for manual provider review, never
blindly retried. Restoring an older database can lose newer send claims; reconcile
provider history before re-enabling reminders after recovery.
