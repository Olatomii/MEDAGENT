"""Consistent SQLite backups; restore only into a new destination."""
import os
import sqlite3
from pathlib import Path
from contextlib import closing
from .database import is_postgres, connection


def snapshot(source,destination):
    if is_postgres(source):
        return postgres_snapshot(source, destination)
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if source==destination:
        raise ValueError('Backup destination must differ from the source.')
    descriptor=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Backup integrity check failed.')
            if dst.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Backup contains invalid foreign-key references.')
            # A downloaded backup must not preserve valid login sessions or invitations.
            tables={r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('staff_sessions','patient_invites','email_verifications','login_limits'):
                if table in tables:
                    dst.execute(f'DELETE FROM {table}')
            dst.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def restore(source,destination):
    # Caller must point the application at the new destination after review.
    return snapshot(source,destination)


# Parent tables precede their dependants. Authentication sessions and one-time
# tokens are intentionally excluded from every downloadable backup.
BACKUP_TABLES = (
    'patients', 'doctors', 'wards', 'sessions', 'appointments', 'visits',
    'events', 'decisions', 'legacy_imports', 'legacy_records', 'doctor_breaks',
    'consultations', 'vitals', 'notifications', 'staff_users', 'staff_audit',
    'duplicate_reviews', 'operational_reviews',
)


def postgres_snapshot(source, destination):
    from .database import initialize
    from .auth import initialize_auth
    from .experience import initialize as initialize_experience
    destination = Path(destination).resolve()
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        initialize(str(destination))
        initialize_auth(str(destination))
        initialize_experience(str(destination))
        with connection(source) as src, connection(str(destination), write=True) as dst:
            src.raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            tables = {r[0] for r in src.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")}
            supported = set(BACKUP_TABLES) | {'staff_sessions', 'patient_invites', 'email_verifications', 'login_limits'}
            if tables - supported:
                raise ValueError('Database contains tables this backup version does not support.')
            dst.execute('CREATE TABLE IF NOT EXISTS legacy_records (appointment_id TEXT PRIMARY KEY REFERENCES appointments,record_json TEXT NOT NULL)')
            for table in BACKUP_TABLES:
                if table not in tables:
                    continue
                columns = [r['name'] for r in dst.execute(f'PRAGMA table_info({table})')]
                # Preserve insertion order for appointment and clinical queue ties.
                if table in ('appointments', 'visits'):
                    columns.insert(0, 'rowid')
                names = ','.join(columns)
                for row in src.execute(f'SELECT {names} FROM {table}'):
                    dst.execute(f'INSERT OR REPLACE INTO {table} ({names}) VALUES ({",".join("?" for _ in columns)})',
                                tuple(row[c] for c in columns))
            if dst.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Backup contains invalid foreign-key references.')
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise
