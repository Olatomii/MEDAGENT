"""Durable, synchronous event handlers. Run again after restart to recover pending work."""
import json
from .database import connection


def emit(conn, kind, entity_id, **payload):
    from .access import actor
    return conn.execute('INSERT INTO events(kind,entity_id,payload,actor_id) VALUES (?,?,?,?)',
                        (kind,entity_id,json.dumps(payload),actor.get())).lastrowid


def decision(conn, event, agent, entity, reason):
    conn.execute('INSERT OR IGNORE INTO decisions(event_id,agent,entity_id,reason) VALUES (?,?,?,?)',
                 (event,agent,entity,reason))


def available_doctor(conn, specialty, date):
    if specialty == 'Emergency / Trauma':
        return emergency_doctor(conn,date)
    # Fulfilled appointments still consume the session's booking allocation.
    # Completing a visit does not create a new bookable appointment.
    return conn.execute('''SELECT s.doctor_id,s.capacity,COUNT(a.appointment_id) AS booked
        FROM sessions s JOIN doctors d ON d.doctor_id=s.doctor_id
        LEFT JOIN appointments a ON a.doctor_id=s.doctor_id AND a.service_date=s.service_date
          AND a.status IN ('CONFIRMED','CHECKED_IN','FULFILLED')
        WHERE d.specialty=? AND s.service_date=? AND s.enabled=1
        GROUP BY s.doctor_id HAVING booked<s.capacity ORDER BY booked,s.doctor_id LIMIT 1''',
        (specialty,date)).fetchone()


def emergency_doctor(conn,date):
    # Emergency session capacity means concurrent places, including reservations
    # and unresolved visits from earlier dates. Admission/completion releases it.
    return conn.execute('''SELECT s.doctor_id,s.capacity,
        ((SELECT COUNT(*) FROM appointments a WHERE a.doctor_id=s.doctor_id
            AND a.specialty='Emergency / Trauma' AND a.status='CONFIRMED') +
         (SELECT COUNT(*) FROM visits v WHERE v.assigned_doctor_id=s.doctor_id
            AND v.state NOT IN ('ADMITTED','COMPLETED','TRANSFER_REQUIRED'))) AS booked
        FROM sessions s JOIN doctors d USING(doctor_id)
        WHERE d.specialty='Emergency / Trauma' AND s.service_date=?
        AND s.enabled=1 AND s.capacity>0 AND booked<s.capacity ORDER BY booked,s.doctor_id LIMIT 1''',(date,)).fetchone()


class WaitlistAgent:
    name = 'Waitlist agent'

    def handle(self, conn, event, data):
        if event['kind'] not in ('CAPACITY_CHANGED','APPOINTMENT_CANCELLED','APPOINTMENT_MISSED'):
            return
        date, specialty = data['date'], data['specialty']
        if data.get('allow_promotion') is False:
            decision(conn,event['event_id'],self.name,event['entity_id'],
                     'Past appointment date: no retrospective waitlist promotion.')
            return
        candidates = conn.execute('''SELECT * FROM appointments
            WHERE service_date=? AND specialty=? AND status='WAITLISTED'
            ORDER BY urgency,COALESCE(queue_entered_at,created_at),rowid''',(date,specialty)).fetchall()
        promoted = 0
        for candidate in candidates:
            doctor = available_doctor(conn,specialty,date)
            if doctor is None:
                break
            conn.execute("UPDATE appointments SET doctor_id=?,status='CONFIRMED' WHERE appointment_id=?",
                         (doctor['doctor_id'],candidate['appointment_id']))
            reason = 'A session place became available. Selected by urgency, then waiting order.'
            decision(conn,event['event_id'],self.name,candidate['appointment_id'],reason)
            emit(conn,'APPOINTMENT_CONFIRMED',candidate['appointment_id'],reason=reason)
            promoted += 1
        if not promoted:
            decision(conn,event['event_id'],self.name,event['entity_id'],
                     'No promotion: no eligible waiting patient or no remaining session capacity.')


class CareCoordinationAgent:
    name = 'Care coordination agent'

    def handle(self, conn, event, data):
        if event['kind'] in ('VISIT_STARTED','VISIT_TRANSITIONED'):
            decision(conn,event['event_id'],self.name,event['entity_id'],data['reason'])


class AppointmentAgent:
    name = 'Appointment agent'

    def handle(self, conn, event, data):
        if event['kind'] in ('APPOINTMENT_CONFIRMED','APPOINTMENT_WAITLISTED','APPOINTMENT_CANCELLED','APPOINTMENT_MISSED','APPOINTMENT_RESCHEDULED','APPOINTMENT_REASSIGNED','LEGACY_IMPORTED'):
            decision(conn,event['event_id'],self.name,event['entity_id'],data['reason'])


class ClinicalSupportAgent:
    def handle(self,conn,event,data):
        agents={'VITALS_RECORDED':'Clinical prep agent','WARD_ADMITTED':'Ward agent',
                'AVAILABILITY_CHANGED':'Availability agent','CONSULTATION_STARTED':'Queue agent',
                'ATTENDANCE_CONFIRMED':'Appointment agent',
                'WARD_DISCHARGED':'Ward agent','WARD_CAPACITY_CHANGED':'Ward agent',
                'BILLING_CLEARED':'Billing agent','REMINDER_ACCEPTED':'Reminder agent',
                'REMINDER_REVIEW_REQUIRED':'Reminder agent','REMINDER_PREFERENCE_CHANGED':'Reminder agent'}
        if event['kind'] in agents:
            decision(conn,event['event_id'],agents[event['kind']],event['entity_id'],data['reason'])


def process_events(path, limit=100):
    processed = 0
    for _ in range(limit):
        event_id = None
        try:
            with connection(path, write=True) as conn:
                event = conn.execute('SELECT * FROM events WHERE processed_at IS NULL ORDER BY event_id LIMIT 1').fetchone()
                if event is None:
                    break
                event_id = event['event_id']
                data = json.loads(event['payload'])
                for agent in (AppointmentAgent(),WaitlistAgent(),CareCoordinationAgent(),ClinicalSupportAgent()):
                    agent.handle(conn,event,data)
                conn.execute('UPDATE events SET processed_at=CURRENT_TIMESTAMP,error=NULL,attempts=attempts+1 WHERE event_id=?',(event_id,))
                processed += 1
        except Exception as exc:
            if event_id is None:
                raise
            with connection(path,write=True) as conn:
                conn.execute('UPDATE events SET error=?,attempts=attempts+1 WHERE event_id=?',
                             (type(exc).__name__,event_id))
            break
    return processed
