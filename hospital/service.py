import datetime as dt
from zoneinfo import ZoneInfo
from .database import connection, identifier, initialize
from .agents import emit, available_doctor, process_events
from reminders import valid_email
from .clinical import ClinicalCare
from .errors import Conflict
from .scheduling import Scheduling


TRANSITIONS = {
    'ASSESSMENT': {'CONSULTATION','TRANSFER_REQUIRED'},
    'CONSULTATION': {'DIAGNOSTICS','PHARMACY','ADMITTED','COMPLETED','TRANSFER_REQUIRED'},
    'DIAGNOSTICS': {'CONSULTATION'},
    'PHARMACY': {'COMPLETED'},
    'ADMITTED': {'COMPLETED','TRANSFER_REQUIRED'},
    'COMPLETED': set(), 'TRANSFER_REQUIRED': set(),
}


class Hospital(ClinicalCare,Scheduling):
    def __init__(self,path,today=None):
        self.path=path
        self.today=today or (lambda:dt.datetime.now(ZoneInfo('Africa/Lagos')).date().isoformat())
        initialize(path)

    def records(self,query,params=()):
        with connection(self.path) as conn:
            return [dict(r) for r in conn.execute(query,params)]

    def create_patient(self,name,age,gender,email=''):
        name,email=name.strip(),email.strip()
        if not name or not 0<=age<=120 or (email and not valid_email(email)):
            raise ValueError('Provide a name, age from 0 to 120, and a valid email if supplied.')
        patient_id=identifier('P')
        with connection(self.path,write=True) as conn:
            conn.execute('INSERT INTO patients(patient_id,name,age,gender,email) VALUES (?,?,?,?,?)',
                         (patient_id,name,age,gender,email))
            emit(conn,'PATIENT_CREATED',patient_id)
        return patient_id

    def set_session(self,doctor_id,date,capacity):
        dt.date.fromisoformat(date)
        if date<self.today() or capacity<0:
            raise ValueError('Choose today or a future date and a non-negative capacity.')
        with connection(self.path,write=True) as conn:
            doctor=conn.execute('SELECT * FROM doctors WHERE doctor_id=?',(doctor_id,)).fetchone()
            if doctor is None:
                raise ValueError('Doctor not found.')
            booked=conn.execute("SELECT COUNT(*) FROM appointments WHERE doctor_id=? AND service_date=? AND status IN ('CONFIRMED','CHECKED_IN','FULFILLED')",(doctor_id,date)).fetchone()[0]
            if doctor['specialty']=='Emergency / Trauma':
                booked=conn.execute('''SELECT
                    (SELECT COUNT(*) FROM appointments WHERE doctor_id=? AND specialty='Emergency / Trauma' AND status='CONFIRMED') +
                    (SELECT COUNT(*) FROM visits WHERE assigned_doctor_id=? AND state NOT IN ('ADMITTED','COMPLETED','TRANSFER_REQUIRED'))''',
                    (doctor_id,doctor_id)).fetchone()[0]
            if capacity<booked:
                raise Conflict('Capacity cannot be below existing allocations. Resolve affected bookings first.')
            conn.execute('INSERT INTO sessions(doctor_id,service_date,capacity) VALUES (?,?,?) ON CONFLICT(doctor_id,service_date) DO UPDATE SET capacity=excluded.capacity',
                         (doctor_id,date,capacity))
            emit(conn,'CAPACITY_CHANGED',str(doctor_id),date=date,specialty=doctor['specialty'])
        process_events(self.path)

    def book(self,patient_id,date,specialty,urgency=5,reminder_opt_in=False):
        dt.date.fromisoformat(date)
        if date<self.today() or urgency not in range(1,6):
            raise ValueError('Choose today or a future date and urgency 1–5.')
        emergency=specialty=='Emergency / Trauma'
        if emergency != (urgency in (1,2)) or (emergency and date!=self.today()):
            raise ValueError('Emergency intake requires urgency 1–2 and today’s date; routine bookings use 3–5.')
        # Resolve older durable capacity events before allocating a new booking.
        process_events(self.path)
        appointment_id=identifier('A')
        with connection(self.path,write=True) as conn:
            patient=conn.execute('SELECT * FROM patients WHERE patient_id=?',(patient_id,)).fetchone()
            if patient is None:
                raise ValueError('Select an existing patient ID.')
            if reminder_opt_in and (emergency or date<=self.today() or not valid_email(patient['email'])):
                raise ValueError('Reminder consent requires a future routine appointment and a valid patient email.')
            if not conn.execute('SELECT 1 FROM doctors WHERE specialty=?',(specialty,)).fetchone():
                raise ValueError('Unknown department.')
            if conn.execute("SELECT 1 FROM appointments WHERE patient_id=? AND service_date=? AND specialty=? AND status IN ('CONFIRMED','WAITLISTED','CHECKED_IN')",(patient_id,date,specialty)).fetchone():
                raise Conflict('This patient already has an active booking for this department and date.')
            doctor=available_doctor(conn,specialty,date)
            # Emergencies must not disappear into a routine waitlist.
            if emergency and doctor is None:
                raise Conflict('No emergency session capacity. Staff must arrange an appropriate escalation or transfer.')
            status='CONFIRMED' if doctor else 'WAITLISTED'
            conn.execute('INSERT INTO appointments(appointment_id,patient_id,service_date,specialty,urgency,doctor_id,status,reminder_opt_in) VALUES (?,?,?,?,?,?,?,?)',
                         (appointment_id,patient_id,date,specialty,urgency,doctor['doctor_id'] if doctor else None,status,int(reminder_opt_in)))
            if emergency:
                conn.execute("UPDATE appointments SET billing_status='EXEMPT' WHERE appointment_id=?",(appointment_id,))
            emit(conn,'APPOINTMENT_'+status,appointment_id,reason=(
                'Allocated to an available doctor session.' if doctor else 'No remaining session places; added to the department waitlist.'))
        process_events(self.path)
        return appointment_id

    def close_booking(self,appointment_id,status):
        if status not in ('CANCELLED','MISSED'):
            raise ValueError('Choose cancellation or missed appointment.')
        with connection(self.path,write=True) as conn:
            a=conn.execute('SELECT * FROM appointments WHERE appointment_id=?',(appointment_id,)).fetchone()
            if a is None or a['status'] not in ('CONFIRMED','WAITLISTED'):
                raise Conflict('Only unstarted bookings can be cancelled or marked missed.')
            if status=='MISSED' and (a['service_date']>=self.today() or a['status']!='CONFIRMED'):
                raise Conflict('Only a confirmed appointment from a past date can be marked missed.')
            conn.execute('UPDATE appointments SET status=? WHERE appointment_id=?',(status,appointment_id))
            emit(conn,'APPOINTMENT_'+status,appointment_id,date=a['service_date'],specialty=a['specialty'],allow_promotion=a['service_date']>=self.today(),reason='Staff recorded '+status.lower()+'.')
        process_events(self.path)

    def reschedule(self,appointment_id,revision,new_date,allow_waitlist=False):
        new_date=dt.date.fromisoformat(new_date).isoformat()
        if new_date<self.today():
            raise ValueError('Choose today or a future date.')
        process_events(self.path)
        with connection(self.path,write=True) as conn:
            a=conn.execute('SELECT * FROM appointments WHERE appointment_id=?',(appointment_id,)).fetchone()
            if a is None or a['revision']!=revision or a['status'] not in ('CONFIRMED','WAITLISTED'):
                raise Conflict('The booking changed or has already started. Refresh before rescheduling.')
            if a['urgency'] in (1,2):
                raise Conflict('Emergency intake cannot be rescheduled as a routine appointment.')
            if a['service_date']==new_date:
                raise ValueError('Choose a different appointment date.')
            if conn.execute("SELECT 1 FROM appointments WHERE patient_id=? AND service_date=? AND specialty=? AND status IN ('CONFIRMED','WAITLISTED','CHECKED_IN') AND appointment_id!=?",
                            (a['patient_id'],new_date,a['specialty'],appointment_id)).fetchone():
                raise Conflict('This patient already has an active booking for that department and date.')
            doctor=available_doctor(conn,a['specialty'],new_date)
            if doctor is None and not allow_waitlist:
                raise Conflict('No place is available on the new date. Your original booking is unchanged. Choose another date or explicitly accept the waitlist.')
            status='CONFIRMED' if doctor else 'WAITLISTED'
            conn.execute('''UPDATE appointments SET service_date=?,doctor_id=?,status=?,revision=revision+1,
                reminder_opt_in=?,attendance_confirmed=0,queue_entered_at=CURRENT_TIMESTAMP WHERE appointment_id=?''',
                (new_date,doctor['doctor_id'] if doctor else None,status,
                 a['reminder_opt_in'] if new_date>self.today() else 0,appointment_id))
            emit(conn,'APPOINTMENT_RESCHEDULED',appointment_id,
                 old_date=a['service_date'],new_date=new_date,revision=revision+1,
                 reason=f"Staff rescheduled {a['service_date']} → {new_date}; new status {status}. Billing record retained.")
            if a['status']=='CONFIRMED':
                emit(conn,'CAPACITY_CHANGED',appointment_id,date=a['service_date'],specialty=a['specialty'],
                     allow_promotion=a['service_date']>=self.today())
        process_events(self.path)
        return status

    def check_in(self,appointment_id):
        visit_id=identifier('V')
        with connection(self.path,write=True) as conn:
            a=conn.execute('SELECT * FROM appointments WHERE appointment_id=?',(appointment_id,)).fetchone()
            if a is None or a['status']!='CONFIRMED' or a['service_date']!=self.today():
                raise Conflict('Check-in requires a confirmed appointment for today.')
            if a['urgency'] not in (1,2) and a['billing_status']!='CLEARED':
                raise Conflict('Routine check-in requires recorded billing clearance. Emergency care is exempt.')
            state='CONSULTATION' if a['urgency'] in (1,2) else 'ASSESSMENT'
            conn.execute('INSERT INTO visits(visit_id,appointment_id,state,assigned_doctor_id,urgency) VALUES (?,?,?,?,?)',
                         (visit_id,appointment_id,state,a['doctor_id'],a['urgency']))
            conn.execute("UPDATE appointments SET status='CHECKED_IN' WHERE appointment_id=?",(appointment_id,))
            emit(conn,'VISIT_STARTED',visit_id,reason='Arrival confirmed by staff. Routed to '+state.lower()+'.')
        process_events(self.path)
        return visit_id

    def transition(self,visit_id,version,target,notes=''):
        with connection(self.path,write=True) as conn:
            visit=conn.execute('SELECT * FROM visits WHERE visit_id=?',(visit_id,)).fetchone()
            if visit is None or visit['version']!=version:
                raise Conflict('This visit changed. Refresh before taking another action.')
            if target not in TRANSITIONS[visit['state']]:
                raise Conflict('This transition is not allowed from '+visit['state']+'.')
            if visit['state']=='ASSESSMENT' and target=='CONSULTATION':
                raise Conflict('Record vital signs to complete the assessment.')
            if target=='ADMITTED':
                raise Conflict('Use ward admission to reserve an available bed.')
            from .scheduling import finish_consultation
            finish_consultation(conn,visit_id)
            if visit['state']=='DIAGNOSTICS' and not notes.strip():
                raise ValueError('Record the diagnostic results before returning to consultation.')
            terminal=target in ('COMPLETED','TRANSFER_REQUIRED')
            conn.execute('''UPDATE visits SET state=?,version=version+1,notes=notes||?,
                completed_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END WHERE visit_id=?''',
                (target,('\n'+notes.strip()) if notes.strip() else '',terminal,visit_id))
            if terminal:
                conn.execute("UPDATE appointments SET status='FULFILLED' WHERE appointment_id=?",(visit['appointment_id'],))
            emit(conn,'VISIT_TRANSITIONED',visit_id,reason=f"Staff requested {visit['state']} → {target}; validated against the current state and version.")
            if visit['state']=='ADMITTED':
                emit(conn,'WARD_DISCHARGED',visit_id,reason='Staff ended the ward stay; the ward place is now available.')
        process_events(self.path)

    def reminder_preference(self,appointment_id,enabled):
        with connection(self.path,write=True) as conn:
            a=conn.execute('''SELECT a.*,p.email FROM appointments a JOIN patients p USING(patient_id)
                WHERE appointment_id=?''',(appointment_id,)).fetchone()
            if a is None or a['status'] not in ('CONFIRMED','WAITLISTED'):
                raise Conflict('Only unstarted bookings can change reminder preferences.')
            if enabled and (a['urgency'] in (1,2) or not valid_email(a['email']) or a['service_date']<=self.today()):
                raise ValueError('Reminders require a future routine appointment and a valid patient email.')
            conn.execute('UPDATE appointments SET reminder_opt_in=? WHERE appointment_id=?',(int(enabled),appointment_id))
            emit(conn,'REMINDER_PREFERENCE_CHANGED',appointment_id,reason='Patient reminder consent '+('enabled.' if enabled else 'withdrawn.'))
        process_events(self.path)

    def update_email(self,patient_id,expected_email,email):
        email=email.strip()
        if email and not valid_email(email):
            raise ValueError('Enter a valid email address or leave it blank.')
        with connection(self.path,write=True) as conn:
            patient=conn.execute('SELECT email FROM patients WHERE patient_id=?',(patient_id,)).fetchone()
            if patient is None or patient['email']!=expected_email:
                raise Conflict('The contact record changed. Refresh before editing.')
            if email==expected_email:
                return
            conn.execute('UPDATE patients SET email=? WHERE patient_id=?',(email,patient_id))
            if 'verified_email' in {r['name'] for r in conn.execute('PRAGMA table_info(patients)')}:
                conn.execute("UPDATE patients SET verified_email='' WHERE patient_id=?",(patient_id,))
            conn.execute("UPDATE appointments SET reminder_opt_in=0 WHERE patient_id=? AND status IN ('CONFIRMED','WAITLISTED')",(patient_id,))
            emit(conn,'REMINDER_PREFERENCE_CHANGED',patient_id,reason='Email updated by staff; existing unstarted reminder consents cleared. Confirm consent for the new address.')
        process_events(self.path)
