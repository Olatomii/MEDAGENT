"""Opt-in, date-only appointment reminders. No Streamlit dependency."""
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import urllib.request
from contextlib import closing
from zoneinfo import ZoneInfo


def local_now():
    return dt.datetime.now(ZoneInfo(os.getenv('REMINDER_TIMEZONE', 'Africa/Lagos')))


def valid_email(value):
    return bool(len(value) <= 254 and re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", value))


def configured():
    return bool(os.getenv('REMINDERS_ENABLED') == 'true' and
                os.getenv('RESEND_API_KEY') and os.getenv('REMINDER_FROM_EMAIL'))


def migrate(conn):
    # Serialize migrations across browser sessions and preserve existing records.
    conn.execute('BEGIN IMMEDIATE')
    for table in ('appointments', 'waitlist'):
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        for name, definition in (('email', "TEXT NOT NULL DEFAULT ''"),
                                 ('reminder_opt_in', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('reminder_date', "TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    conn.execute('''CREATE TABLE IF NOT EXISTS email_reminders (
        reminder_key TEXT PRIMARY KEY, queue_number TEXT NOT NULL,
        appointment_date TEXT NOT NULL, status TEXT NOT NULL,
        provider_id TEXT, updated_at TEXT NOT NULL, error_code TEXT)''')
    conn.commit()


def message(date, specialty):
    return (f'This is a reminder of your confirmed appointment on {date}.\n'
            f'Department: {specialty}.\n\n'
            'Please attend on your appointment date and contact the front desk '
            'if you cannot attend. Consultation order depends on availability '
            'and urgency; no fixed consultation time is assigned.\n\nMedAgent Sync')


def send_email(recipient, body, key):
    payload = json.dumps({'from': os.environ['REMINDER_FROM_EMAIL'],
                          'to': [recipient], 'subject': 'Appointment reminder',
                          'text': body}).encode()
    request = urllib.request.Request('https://api.resend.com/emails', data=payload,
        headers={'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY'],
                 'Content-Type': 'application/json', 'Idempotency-Key': key}, method='POST')
    with urllib.request.urlopen(request, timeout=15) as response:
        provider_id = json.load(response).get('id')
        if not provider_id:
            raise ValueError('Missing provider receipt')
        return provider_id


def dispatch(db_path, now=None, sender=None):
    """One attempt per appointment/date; ambiguous failures need manual review.

    Claim before network I/O. Do not retry SENDING/REVIEW_REQUIRED automatically:
    a provider may have accepted the request before a timeout or process crash.
    """
    if sender is None:
        if not configured():
            return 0
        sender = send_email
    now = now or local_now()
    if now.hour < 9:
        return 0
    tomorrow = (now.date() + dt.timedelta(days=1)).isoformat()
    sent = 0
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        candidates = conn.execute('''SELECT a.queue_number FROM appointments a
            WHERE a.reminder_opt_in=1 AND a.reminder_date=? AND a.booking_date=?
            AND a.status='WAITING' AND a.location='Nurses Station'
            AND a.triage_level BETWEEN 3 AND 5 AND a.payment_status='Cleared' ''',
            (tomorrow, tomorrow)).fetchall()
        for candidate in candidates:
            conn.execute('BEGIN IMMEDIATE')
            # Revalidate in the claim transaction; exclude changed/cancelled visits.
            row = conn.execute('''SELECT a.*, d.specialty FROM appointments a
                JOIN doctors d ON d.doc_id=a.doc_id WHERE a.queue_number=?
                AND a.reminder_opt_in=1 AND a.reminder_date=? AND a.booking_date=?
                AND a.status='WAITING' AND a.location='Nurses Station'
                AND a.triage_level BETWEEN 3 AND 5 AND a.payment_status='Cleared'
                AND d.specialty!='Emergency / Trauma' ''',
                (candidate['queue_number'], tomorrow, tomorrow)).fetchone()
            if row is None or not valid_email(row['email']):
                conn.rollback()
                continue
            key = hashlib.sha256(f"{row['queue_number']}:{tomorrow}".encode()).hexdigest()
            inserted = conn.execute('''INSERT OR IGNORE INTO email_reminders
                (reminder_key,queue_number,appointment_date,status,updated_at)
                VALUES (?,?,?,'SENDING',?)''', (key, row['queue_number'], tomorrow, now.isoformat())).rowcount
            conn.commit()
            if not inserted:
                continue
            try:
                receipt = sender(row['email'], message(tomorrow, row['specialty']), key)
            except Exception as exc:
                # Do not persist provider response bodies, addresses, or credentials.
                with conn:
                    conn.execute("UPDATE email_reminders SET status='REVIEW_REQUIRED',error_code=?,updated_at=? WHERE reminder_key=?",
                                 (type(exc).__name__, now.isoformat(), key))
                continue
            with conn:
                conn.execute("UPDATE email_reminders SET status='ACCEPTED',provider_id=?,updated_at=? WHERE reminder_key=?",
                             (receipt, now.isoformat(), key))
                conn.execute('INSERT INTO audit_events (message,is_critical) VALUES (?,0)',
                             (f'[REMINDER_AGENT] Email accepted by provider for appointment {tomorrow} ({row["queue_number"]}).',))
            sent += 1
    return sent


def start_worker(db_path):
    stop = threading.Event()
    def run():
        while not stop.is_set():
            try:
                dispatch(db_path)
            except Exception:
                logging.getLogger(__name__).warning('Reminder check failed; will check again in 60 seconds.')
            stop.wait(60)
    thread = threading.Thread(target=run, name='appointment-reminders', daemon=True)
    thread.start()
    return stop
