"""Patient operations expose only records owned by the authenticated patient."""
import hashlib
import hmac
import os
import secrets
import time
from .accounts import require_user
from .database import connection
from .access import actor
from .agents import emit
from .auth import AccessDenied
from .service import Hospital
import reminders


class PatientPortal:
    def __init__(self,path,token):
        self.path,self.token=path,token

    @property
    def user(self):
        user=require_user(self.path,self.token,{'patient'})
        if not user['patient_id']:
            raise AccessDenied('Patient identity is not linked.')
        return user

    def profile(self):
        user=self.user
        with connection(self.path) as conn:
            return dict(conn.execute('SELECT patient_id,name,email,verified_email FROM patients WHERE patient_id=?',(user['patient_id'],)).fetchone())

    def appointments(self):
        user=self.user
        with connection(self.path) as conn:
            return [dict(r) for r in conn.execute('''SELECT a.appointment_id,a.service_date,a.specialty,a.status,
                a.billing_status,a.reminder_opt_in,a.revision,a.attendance_confirmed,d.name AS doctor
                FROM appointments a LEFT JOIN doctors d ON d.doctor_id=a.doctor_id
                WHERE a.patient_id=? ORDER BY a.service_date DESC''',(user['patient_id'],))]

    def _owned(self,appointment_id):
        user=self.user
        with connection(self.path) as conn:
            if not conn.execute('SELECT 1 FROM appointments WHERE appointment_id=? AND patient_id=?',(appointment_id,user['patient_id'])).fetchone():
                raise AccessDenied('Appointment is not accessible.')
        return user

    def _invoke(self,user,method,*args):
        marker=actor.set(user['user_id'])
        try:
            return getattr(Hospital(self.path),method)(*args)
        finally:
            actor.reset(marker)

    def book(self,date,specialty,consent=False):
        user=self.user
        if specialty not in ('General Practice','Cardiology','Orthopedics'):
            raise ValueError('Only routine appointments can be requested in the portal.')
        return self._invoke(user,'book',user['patient_id'],date,specialty,5,consent)

    def reschedule(self,appointment_id,revision,date,allow_waitlist=False):
        return self._invoke(self._owned(appointment_id),'reschedule',appointment_id,revision,date,allow_waitlist)

    def cancel(self,appointment_id):
        return self._invoke(self._owned(appointment_id),'close_booking',appointment_id,'CANCELLED')

    def consent(self,appointment_id,enabled):
        return self._invoke(self._owned(appointment_id),'reminder_preference',appointment_id,enabled)

    def confirm_attendance(self,appointment_id):
        user=self._owned(appointment_id)
        today=Hospital(self.path).today()
        marker=actor.set(user['user_id'])
        try:
            with connection(self.path,write=True) as conn:
                changed=conn.execute("UPDATE appointments SET attendance_confirmed=1 WHERE appointment_id=? AND patient_id=? AND status='CONFIRMED' AND service_date>=?",
                                     (appointment_id,user['patient_id'],today)).rowcount
                if not changed:
                    raise ValueError('Only an upcoming confirmed appointment can confirm attendance.')
                emit(conn,'ATTENDANCE_CONFIRMED',appointment_id,reason='Patient confirmed planned attendance; this is not clinical check-in.')
        finally:
            actor.reset(marker)

    def request_verification(self,sender=None):
        user=self.user
        profile=self.profile()
        if not reminders.valid_email(profile['email']):
            raise ValueError('Ask the front desk to record a valid email first.')
        if sender is None:
            if os.getenv('MEDAGENT_V2_EMAIL_VERIFICATION_ENABLED')!='true' or not os.getenv('RESEND_API_KEY') or not os.getenv('REMINDER_FROM_EMAIL'):
                raise ValueError('Email verification delivery has not been activated.')
            sender=lambda to,body,key:reminders.send_email(to,body,key,subject='MedAgent email verification')
        code=secrets.token_urlsafe(24)
        now=time.time()
        with connection(self.path,write=True) as conn:
            old=conn.execute('SELECT requested_at FROM email_verifications WHERE user_id=?',(user['user_id'],)).fetchone()
            if old and now-old['requested_at']<60:
                raise ValueError('Wait one minute before requesting another verification email.')
            conn.execute('''INSERT INTO email_verifications(user_id,email,code_hash,expires_at,requested_at,attempts)
                VALUES (?,?,?,?,?,0) ON CONFLICT(user_id) DO UPDATE SET email=excluded.email,code_hash=excluded.code_hash,
                expires_at=excluded.expires_at,requested_at=excluded.requested_at,attempts=0''',
                (user['user_id'],profile['email'],hashlib.sha256(code.encode()).hexdigest(),now+900,now))
        try:
            sender(profile['email'],'Your MedAgent verification code is:\n'+code+'\nIt expires in 15 minutes.',hashlib.sha256(('verify:'+code).encode()).hexdigest())
        except Exception:
            raise ValueError('Verification delivery could not be confirmed. Try again after one minute.') from None

    def verify_email(self,code):
        user=self.user
        with connection(self.path,write=True) as conn:
            row=conn.execute('SELECT * FROM email_verifications WHERE user_id=?',(user['user_id'],)).fetchone()
            email=conn.execute('SELECT email FROM patients WHERE patient_id=?',(user['patient_id'],)).fetchone()[0]
            valid=bool(row and row['attempts']<5 and row['expires_at']>time.time() and row['email']==email and
                       hmac.compare_digest(row['code_hash'],hashlib.sha256(code.strip().encode()).hexdigest()))
            if valid:
                conn.execute('UPDATE patients SET verified_email=? WHERE patient_id=?',(email,user['patient_id']))
                conn.execute('DELETE FROM email_verifications WHERE user_id=?',(user['user_id'],))
                conn.execute("INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,'VERIFY_EMAIL','SUCCESS')",(user['user_id'],))
            elif row:
                conn.execute('UPDATE email_verifications SET attempts=attempts+1 WHERE user_id=?',(user['user_id'],))
        if not valid:
            raise ValueError('Verification code is invalid or expired.')
