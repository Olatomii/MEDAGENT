"""Appointment reminders for the v2 schema; no external send without explicit activation."""
import datetime as dt
import hashlib
import os
import reminders
from .database import connection
from .agents import emit


def configured():
    return bool(os.getenv('MEDAGENT_V2_REMINDERS_ENABLED')=='true' and
                os.getenv('RESEND_API_KEY') and os.getenv('REMINDER_FROM_EMAIL'))


def suggested_email(email):
    local,sep,domain=email.strip().rpartition('@')
    fixes={'gmail.co':'gmail.com','gamil.com':'gmail.com','gmial.com':'gmail.com',
           'yahoo.co':'yahoo.com','outlok.com':'outlook.com','hotmai.com':'hotmail.com'}
    return local+'@'+fixes[domain.lower()] if sep and domain.lower() in fixes else None


def dispatch(path,now=None,sender=None):
    if sender is None:
        if not configured():
            return 0
        sender=reminders.send_email
    now=now or reminders.local_now()
    if now.hour<9:
        return 0
    date=(now.date()+dt.timedelta(days=1)).isoformat()
    with connection(path) as conn:
        candidates=conn.execute("SELECT appointment_id FROM appointments WHERE service_date=? AND status='CONFIRMED' AND reminder_opt_in=1",(date,)).fetchall()
    sent=0
    for candidate in candidates:
        with connection(path,write=True) as conn:
            row=conn.execute('''SELECT a.*,p.email FROM appointments a JOIN patients p USING(patient_id)
                WHERE a.appointment_id=? AND a.service_date=? AND a.status='CONFIRMED'
                AND a.urgency BETWEEN 3 AND 5 AND a.billing_status='CLEARED' AND a.reminder_opt_in=1''',
                (candidate['appointment_id'],date)).fetchone()
            if row is None or not reminders.valid_email(row['email']):
                continue
            # Keep revision-zero keys compatible with already recorded deliveries.
            suffix=f":revision:{row['revision']}" if row['revision'] else ''
            key=hashlib.sha256(f"v2:{row['appointment_id']}:{date}{suffix}".encode()).hexdigest()
            claimed=conn.execute('''INSERT OR IGNORE INTO notifications(reminder_key,appointment_id,appointment_date,status,updated_at)
                VALUES (?,?,?,'SENDING',?)''',(key,row['appointment_id'],date,now.isoformat())).rowcount
        if not claimed:
            continue
        try:
            receipt=sender(row['email'],reminders.message(date,row['specialty']),key)
            if not receipt:
                raise ValueError('Missing provider receipt')
        except Exception as exc:
            with connection(path,write=True) as conn:
                conn.execute("UPDATE notifications SET status='REVIEW_REQUIRED',error_code=?,updated_at=? WHERE reminder_key=?",
                             (type(exc).__name__,now.isoformat(),key))
                emit(conn,'REMINDER_REVIEW_REQUIRED',row['appointment_id'],reason='Delivery attempt needs provider verification. No automatic retry.')
            continue
        with connection(path,write=True) as conn:
            conn.execute("UPDATE notifications SET status='ACCEPTED',provider_id=?,updated_at=? WHERE reminder_key=?",(receipt,now.isoformat(),key))
            emit(conn,'REMINDER_ACCEPTED',row['appointment_id'],reason='Provider accepted the reminder; inbox delivery is not confirmed.')
        sent+=1
    return sent
