"""Consistent SQLite backups; restore only into a new destination."""
import os
import sqlite3
from pathlib import Path
from contextlib import closing


def snapshot(source,destination):
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
