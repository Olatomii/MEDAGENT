# Staff access in the development application

`app_v2.py` now requires staff sign-in before displaying patients or executing
application commands. The original `app.py` and current preview are unchanged.
No staff accounts or default credentials are committed or created automatically.
A persistent database is required to retain accounts.

The preview supports protected initial setup: copy `MEDAGENT_SETUP_TOKEN` from
the service's private Render Environment settings into the initial setup screen,
then choose your username and password. The token is randomly provisioned outside
Git. Setup only works while the staff table is empty and is serialized against
concurrent attempts. Remove the token after setup on persistent hosting. On the
current ephemeral free preview, database loss can reopen setup; only someone with
the private token can create the first account again. Do not use real patient data.

`MEDAGENT_APP_VERSION=v2` switches the existing app.py deployment entry point to
the authenticated version. Removing that setting restores the legacy entry point;
it does not migrate or delete either database. V2 reminders remain disabled until
explicitly configured and run with the worker.

## Initial setup on the trusted host

Use the same database file configured in `MEDAGENT_V2_DB_PATH`:

```sh
python -m hospital.manage --database medagent_v2.db create-staff administrator --role admin
python -m streamlit run app_v2.py
```

The command prompts twice with hidden input; do not place a password in command
arguments, Git, logs or chat. Create named staff accounts with the same command
and the appropriate role. Maintenance commands require trusted access to the host
and database; they intentionally do not accept web sessions.

| Role | Workspaces and permitted commands |
|---|---|
| admin | All workspaces and actions, capacity settings, event/audit inspection |
| front_desk | Patient contact and booking records, scheduling, cancellation, billing clearance, check-in and reminder consent; no clinical-table reads |
| nurse | Clinical workspace and vital-sign recording |
| physician | Clinical and ward workspaces; consultation, diagnostics and ward care transitions |
| pharmacy | Pharmacy queue and completion of visits currently in pharmacy |
| ward | Ward workspace, discharge/transfer, reconciliation of already-admitted unassigned patients |

Clinical staff currently share clinical chart read access; this is role-based
access, not assignment-based patient isolation. There is no patient portal yet.
Capacity changes and event processing controls are administrator-only in the UI.

## Session and password behavior

Passwords use per-user random salts and PBKDF2-HMAC-SHA256 with 600,000 iterations.
Only the derived hash is stored. Passwords must be 12–256 characters. Session
tokens are random, kept in server-side Streamlit session state, and stored only
as SHA-256 digests in SQLite. They expire after eight hours. Logout clears session
state, including cached widgets, and revokes the database session.

Each service command and data read revalidates the current session. Five failed
logins for a username cause a 15-minute cooldown, including unknown usernames.
Sign-in errors do not reveal whether an account exists. Additional network-wide
rate limiting, MFA/SSO and account recovery identity checks remain deployment work.

Trusted-host account administration:

```sh
python -m hospital.manage --database medagent_v2.db disable-staff username
python -m hospital.manage --database medagent_v2.db reset-password username
```

Disabling an account or resetting its password revokes all its sessions. A reset
does not re-enable a disabled account. The last enabled administrator cannot be
disabled. Username/role changes and user self-service are not yet implemented.

## Enforcement and attribution

The web app uses `StaffHospital`, which restricts commands, checks role-specific
care states and applies SQLite read authorizers. Clinical states still use the
existing optimistic version checks. Hiding navigation is a convenience; denied
commands fail at the application boundary even when called without the UI.
The underlying `Hospital` class is reserved for trusted code, tests and maintenance;
do not expose it directly from future HTTP endpoints. This is not protection
against someone who controls the Python process or database files.

Business events record the initiating staff ID in the same transaction as the
change. Failed permission checks and sign-in/out actions appear in `staff_audit`.
Passwords, session tokens and clinical notes are not copied into that audit log.
Trusted-host actions are explicitly labelled HOST; the user ID in those entries
is the affected account, not a claim of an authenticated administrator session.
Older and unattended events can have no staff actor. Agent decisions link to their
triggering event; automatic decisions during synchronous processing may retain the
initiating staff actor. Audit data is not an immutable, tamper-proof external log.

Use HTTPS and persistent storage before exposing sign-in remotely. This milestone
has not changed hosting, activated real emails, created remote staff accounts or
established production security/compliance readiness.
