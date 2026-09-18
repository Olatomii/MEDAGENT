import math
from .database import connection
from .agents import emit, process_events, emergency_doctor
from .errors import Conflict


def current_visit(conn,visit_id,version,state):
    visit=conn.execute('SELECT * FROM visits WHERE visit_id=?',(visit_id,)).fetchone()
    if visit is None or visit['version']!=version or visit['state']!=state:
        raise Conflict('This visit changed or is not in the required care state. Refresh first.')
    return visit


class ClinicalCare:
    def clear_billing(self,appointment_id,reference):
        if not reference.strip():
            raise ValueError('Enter a staff billing reference. This records clearance; it does not charge money.')
        with connection(self.path,write=True) as conn:
            a=conn.execute('SELECT * FROM appointments WHERE appointment_id=?',(appointment_id,)).fetchone()
            if a is None or a['status'] not in ('CONFIRMED','WAITLISTED') or a['billing_status']!='PENDING':
                raise Conflict('Only a pending, unstarted routine booking can be cleared.')
            conn.execute("UPDATE appointments SET billing_status='CLEARED',billing_reference=? WHERE appointment_id=?",(reference.strip(),appointment_id))
            emit(conn,'BILLING_CLEARED',appointment_id,reason='Staff recorded routine billing clearance.')
        process_events(self.path)

    def record_vitals(self,visit_id,version,systolic,temperature,heart_rate,spo2,respiratory_rate):
        readings=(systolic,temperature,heart_rate,spo2,respiratory_rate)
        bounds=((0,300),(20,45),(0,250),(0,100),(0,60))
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) or not lo<=x<=hi
               for x,(lo,hi) in zip(readings,bounds)):
            raise ValueError('Vital readings must be finite numbers within the input ranges.')
        # Preserve prototype rules. These are not validated clinical triage criteria.
        flagged=systolic>180 or temperature>39 or heart_rate>120 or spo2<92 or respiratory_rate>24
        with connection(self.path,write=True) as conn:
            visit=current_visit(conn,visit_id,version,'ASSESSMENT')
            doctor=emergency_doctor(conn,self.today()) if flagged else None
            target='TRANSFER_REQUIRED' if flagged and doctor is None else 'CONSULTATION'
            reason=('Prototype vital-sign rule flagged urgent staff review. '+
                    ('Emergency place reserved.' if doctor else 'No staffed emergency place: transfer required.')) if flagged else 'Vitals recorded; routed to consultation.'
            conn.execute('INSERT INTO vitals(visit_id,systolic,temperature,heart_rate,spo2,respiratory_rate,flagged) VALUES (?,?,?,?,?,?,?)',
                         (visit_id,*readings,int(flagged)))
            conn.execute('''UPDATE visits SET state=?,version=version+1,assigned_doctor_id=?,urgency=?,
                notes=notes||?,completed_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END WHERE visit_id=?''',
                (target,doctor['doctor_id'] if doctor else visit['assigned_doctor_id'],2 if flagged else visit['urgency'],
                 '\n'+reason,target=='TRANSFER_REQUIRED',visit_id))
            if target=='TRANSFER_REQUIRED':
                conn.execute("UPDATE appointments SET status='FULFILLED' WHERE appointment_id=?",(visit['appointment_id'],))
            emit(conn,'VITALS_RECORDED',visit_id,reason=reason)
        process_events(self.path)

    def set_ward_capacity(self,ward_id,capacity):
        if not isinstance(capacity,int) or capacity<0:
            raise ValueError('Capacity must be a non-negative integer.')
        with connection(self.path,write=True) as conn:
            if not conn.execute('SELECT 1 FROM wards WHERE ward_id=?',(ward_id,)).fetchone():
                raise ValueError('Ward not found.')
            occupied=conn.execute("SELECT COUNT(*) FROM visits WHERE ward_id=? AND state='ADMITTED'",(ward_id,)).fetchone()[0]
            if capacity<occupied:
                raise Conflict('Capacity cannot be lower than the current ward occupancy.')
            conn.execute('UPDATE wards SET capacity=? WHERE ward_id=?',(capacity,ward_id))
            emit(conn,'WARD_CAPACITY_CHANGED',str(ward_id),reason='Staff configured ward capacity: '+str(capacity)+'.')
        process_events(self.path)

    def admit(self,visit_id,version,ward_id,notes=''):
        with connection(self.path,write=True) as conn:
            existing=conn.execute('SELECT * FROM visits WHERE visit_id=?',(visit_id,)).fetchone()
            source='ADMITTED' if existing and existing['state']=='ADMITTED' and existing['ward_id'] is None else 'CONSULTATION'
            current_visit(conn,visit_id,version,source)
            ward=conn.execute('SELECT * FROM wards WHERE ward_id=?',(ward_id,)).fetchone()
            if ward is None:
                raise ValueError('Select a ward.')
            occupied=conn.execute("SELECT COUNT(*) FROM visits WHERE state='ADMITTED' AND ward_id=?",(ward_id,)).fetchone()[0]
            if occupied>=ward['capacity']:
                raise Conflict('No configured bed is available in this ward. Patient remains in consultation.')
            conn.execute("UPDATE visits SET state='ADMITTED',ward_id=?,version=version+1,notes=notes||? WHERE visit_id=?",
                         (ward_id,'\n[WARD] '+ward['name']+' '+notes.strip(),visit_id))
            emit(conn,'WARD_ADMITTED',visit_id,reason='Staff ordered admission; reserved a place in '+ward['name']+'.')
        process_events(self.path)
