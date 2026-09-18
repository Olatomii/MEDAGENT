import sqlite3
import uuid
from contextlib import contextmanager


def identifier(prefix):
    return prefix + '-' + uuid.uuid4().hex.upper()


@contextmanager
def connection(path, write=False):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        if write:
            conn.execute('BEGIN IMMEDIATE')
        yield conn
        if write:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(path):
    with connection(path, write=True) as conn:
        # Never silently reinterpret the original application's database.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='appointments'").fetchone():
            columns = {r['name'] for r in conn.execute('PRAGMA table_info(appointments)')}
            if 'appointment_id' not in columns:
                raise ValueError('Use a separate database for the new version; import the original read-only.')
        statements = [
            '''CREATE TABLE IF NOT EXISTS patients (
                patient_id TEXT PRIMARY KEY, name TEXT NOT NULL, age INTEGER NOT NULL CHECK(age BETWEEN 0 AND 120),
                gender TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
            '''CREATE TABLE IF NOT EXISTS doctors (
                doctor_id INTEGER PRIMARY KEY, name TEXT NOT NULL, specialty TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS sessions (
                doctor_id INTEGER REFERENCES doctors, service_date TEXT NOT NULL,
                capacity INTEGER NOT NULL CHECK(capacity>=0), PRIMARY KEY(doctor_id,service_date))''',
            '''CREATE TABLE IF NOT EXISTS appointments (
                appointment_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients,
                service_date TEXT NOT NULL, specialty TEXT NOT NULL, urgency INTEGER NOT NULL CHECK(urgency BETWEEN 1 AND 5),
                doctor_id INTEGER REFERENCES doctors, status TEXT NOT NULL
                CHECK(status IN ('CONFIRMED','WAITLISTED','CHECKED_IN','CANCELLED','MISSED','FULFILLED')),
                reminder_opt_in INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
            '''CREATE TABLE IF NOT EXISTS visits (
                visit_id TEXT PRIMARY KEY, appointment_id TEXT NOT NULL UNIQUE REFERENCES appointments,
                state TEXT NOT NULL CHECK(state IN ('ASSESSMENT','CONSULTATION','DIAGNOSTICS','PHARMACY','ADMITTED','COMPLETED','TRANSFER_REQUIRED')),
                notes TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0,
                checked_in_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT)''',
            '''CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                processed_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, error TEXT)''',
            '''CREATE TABLE IF NOT EXISTS decisions (
                decision_id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL REFERENCES events,
                agent TEXT NOT NULL, entity_id TEXT NOT NULL, reason TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(event_id,agent,entity_id))''',
            '''CREATE TABLE IF NOT EXISTS legacy_imports (
                source TEXT NOT NULL, source_table TEXT NOT NULL, source_row INTEGER NOT NULL,
                appointment_id TEXT NOT NULL REFERENCES appointments,
                PRIMARY KEY(source,source_table,source_row))''',
            'CREATE INDEX IF NOT EXISTS appointments_session ON appointments(service_date,doctor_id,status)',
            'CREATE INDEX IF NOT EXISTS pending_events ON events(processed_at,event_id)',
        ]
        for statement in statements:
            conn.execute(statement)
        for i, name, specialty in [(1,'Dr. Smith','General Practice'),(2,'Dr. Taylor','General Practice'),
                (3,'Dr. Jones','Cardiology'),(4,'Dr. Davis','Cardiology'),(5,'Dr. Brown','Orthopedics'),
                (6,'Dr. Wilson','Orthopedics'),(7,'Dr. Evans','Emergency / Trauma'),
                (8,'Dr. Carter','Emergency / Trauma'),(9,'Dr. Mitchell','Emergency / Trauma')]:
            conn.execute('INSERT OR IGNORE INTO doctors VALUES (?,?,?)',(i,name,specialty))
