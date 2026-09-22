"""Daily native SQLite backups and independently monitored maintenance jobs."""
import datetime as dt
import fcntl
import logging
import os
from pathlib import Path
import re
import tempfile
from .backup import snapshot
from .database import connection
from .agents import process_events
from .notifications import dispatch,configured

LOG=logging.getLogger(__name__)


def initialize_status(path):
    with connection(path,write=True) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS maintenance_status (
            job TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, succeeded_at TEXT,
            state TEXT NOT NULL, detail TEXT NOT NULL)''')


def record(path,job,state,detail,now):
    with connection(path,write=True) as conn:
        conn.execute('''INSERT INTO maintenance_status(job,attempted_at,succeeded_at,state,detail)
            VALUES (?,?,?,?,?) ON CONFLICT(job) DO UPDATE SET attempted_at=excluded.attempted_at,
            succeeded_at=COALESCE(excluded.succeeded_at,maintenance_status.succeeded_at),
            state=excluded.state,detail=excluded.detail''',
            (job,now.isoformat(),now.isoformat() if state=='OK' else None,state,detail))


def daily_backup(path,directory,now=None,keep=14):
    """One complete backup per UTC date; rotate only after a successful copy."""
    if keep<2:
        raise ValueError('Retain at least two backups.')
    now=now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Use a timezone-aware backup date.')
    directory=Path(directory).resolve()
    if directory==Path(path).resolve().parent:
        raise ValueError('Backups require a separate directory.')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Lock prevents two workers racing rotation or publishing partial files.
    with (directory/'.backup.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        target=directory/f"medagent-{now.astimezone(dt.timezone.utc).date().isoformat()}.db"
        if not target.exists():
            with tempfile.TemporaryDirectory(prefix='.pending-',dir=directory) as tmp:
                ready=snapshot(path,Path(tmp)/'snapshot.db')
                with ready.open('rb') as file:
                    os.fsync(file.fileno())
                os.replace(ready,target)
            descriptor=os.open(directory,os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        backups=sorted(p for p in directory.iterdir() if re.fullmatch(r'medagent-\d{4}-\d{2}-\d{2}\.db',p.name) and p.is_file())
        for old in backups[:-keep]:
            if old.resolve()!=Path(path).resolve():
                old.unlink()
        return target


def run_once(path,backup_dir=None,now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    initialize_status(path)
    jobs=[('events',lambda:f'Processed {process_events(path)} events.'),
          ('reminders',lambda:f'Provider accepted {dispatch(path)} reminders.')]
    if backup_dir:
        jobs.append(('backup',lambda:'Snapshot available: '+daily_backup(path,backup_dir,now).name))
    else:
        record(path,'backup','DISABLED','Automatic backup directory is not configured.',now)
    for name,job in jobs:
        if name=='reminders' and not configured():
            record(path,name,'DISABLED','Email delivery is not configured or enabled.',now)
            continue
        try:
            detail=job()
            if name=='events':
                with connection(path) as conn:
                    count=conn.execute('SELECT COUNT(*) FROM events WHERE processed_at IS NULL AND error IS NOT NULL').fetchone()[0]
                if count:
                    record(path,name,'REVIEW_REQUIRED',f'{count} pending events have processing errors.',now)
                    continue
            if name=='reminders':
                with connection(path) as conn:
                    count=conn.execute("SELECT COUNT(*) FROM notifications WHERE status IN ('SENDING','REVIEW_REQUIRED')").fetchone()[0]
                if count:
                    record(path,name,'REVIEW_REQUIRED',f'{count} delivery attempts need provider review. No automatic resend.',now)
                    continue
            record(path,name,'OK',detail,now)
        except Exception as exc:
            # Provider/DB exceptions may contain addresses or credentials: log type only.
            LOG.error('Maintenance job %s failed (%s)',name,type(exc).__name__)
            record(path,name,'ERROR',type(exc).__name__,now)
    record(path,'worker','OK','Maintenance cycle completed; inspect individual jobs.',now)
