"""Patient registration, operational review and booking reassignment."""
import datetime as dt
import secrets
from zoneinfo import ZoneInfo
from .database import connection,identifier
from .auth import password_digest,AccessDenied
from .accounts import require_user,audit
from .agents import emit,process_events
from .errors import Conflict
from reminders import valid_email


def initialize(path):
    with connection(path,write=True) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS duplicate_reviews (
            patient_id TEXT REFERENCES patients, candidate_id TEXT REFERENCES patients,
            resolved INTEGER NOT NULL DEFAULT 0, resolution TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(patient_id,candidate_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS operational_reviews (
            item_key TEXT PRIMARY KEY, note TEXT NOT NULL, user_id INTEGER,
            reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def matches(c,name,age,email):
    return [dict(r) for r in c.execute('''SELECT patient_id,name,age,email FROM patients
        WHERE (trim(lower(name))=trim(lower(?)) AND age=?)
           OR (?!='' AND lower(trim(email))=lower(trim(?)))''',(name,age,email,email))]


def register(path,name,age,gender,email,username,password):
    name,email,username=name.strip(),email.strip(),username.strip().lower()
    if not name or len(name)>120 or not 0<=age<=120 or not valid_email(email):
        raise ValueError('Enter your name, age from 0 to 120, and a valid email address.')
    if not 3<=len(username)<=80 or not all(c.isalnum() or c in '._-' for c in username) or not 12<=len(password)<=256:
        raise ValueError('Use a username of 3–80 letters, digits, dots, underscores or hyphens and a password of 12–256 characters.')
    initialize(path)
    salt=secrets.token_hex(16)
    digest=password_digest(password,salt)
    with connection(path,write=True) as c:
        if not c.execute("SELECT 1 FROM staff_users WHERE role='admin' AND enabled=1").fetchone():
            raise ValueError('Hospital administrator setup must be completed first.')
        if c.execute('SELECT 1 FROM staff_users WHERE username=?',(username,)).fetchone():
            raise ValueError('That username is unavailable. Choose another or contact the hospital for help signing in.')
        candidates=matches(c,name,age,email)
        # Always a NEW identity. Never attach unverified self-registration to an old record.
        pid=identifier('P')
        c.execute('INSERT INTO patients(patient_id,name,age,gender,email) VALUES (?,?,?,?,?)',(pid,name,age,gender,email))
        uid=c.execute("INSERT INTO staff_users(username,role,salt,password_hash,patient_id) VALUES (?,'patient',?,?,?)",(username,salt,digest,pid)).lastrowid
        for candidate in candidates:
            c.execute('INSERT INTO duplicate_reviews(patient_id,candidate_id) VALUES (?,?)',(pid,candidate['patient_id']))
        c.execute("INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,'SELF_REGISTER','SUCCESS')",(uid,))
        emit(c,'PATIENT_CREATED',pid)
    return pid


def duplicates(path,token):
    require_user(path,token,{'admin','front_desk'})
    initialize(path)
    with connection(path) as c:
        return [dict(r) for r in c.execute('''SELECT r.*,p.name,p.email,q.name AS candidate_name,q.email AS candidate_email
            FROM duplicate_reviews r JOIN patients p ON p.patient_id=r.patient_id
            JOIN patients q ON q.patient_id=r.candidate_id WHERE resolved=0''')]


def resolve_duplicate(path,token,patient_id,candidate_id,note):
    user=require_user(path,token,{'admin','front_desk'})
    if len(note.strip())<10:
        raise ValueError('Record the identity checks and outcome (at least 10 characters).')
    with connection(path,write=True) as c:
        changed=c.execute('UPDATE duplicate_reviews SET resolved=1,resolution=? WHERE patient_id=? AND candidate_id=? AND resolved=0',
                          (note.strip(),patient_id,candidate_id)).rowcount
        if not changed: raise Conflict('Review already resolved or not found.')
        audit(c,user,'RESOLVE_DUPLICATE_REVIEW',patient_id)


def reassignment_options(path,token,appointment_id):
    require_user(path,token,{'admin','front_desk'})
    with connection(path) as c:
        a=c.execute('SELECT * FROM appointments WHERE appointment_id=?',(appointment_id,)).fetchone()
        if not a or a['status']!='CONFIRMED' or a['urgency']<=2: return []
        return [dict(r) for r in c.execute('''SELECT d.doctor_id,d.name,s.capacity,
            (SELECT COUNT(*) FROM appointments b WHERE b.doctor_id=d.doctor_id AND b.service_date=s.service_date
            AND b.status IN ('CONFIRMED','CHECKED_IN','FULFILLED')) AS booked
            FROM doctors d JOIN sessions s USING(doctor_id) WHERE d.specialty=? AND s.service_date=?
            AND s.enabled=1 AND d.doctor_id!=? AND booked<s.capacity ORDER BY booked,d.doctor_id''',
            (a['specialty'],a['service_date'],a['doctor_id']))]


def reassign(path,token,appointment_id,revision,doctor_id,reason):
    user=require_user(path,token,{'admin','front_desk'})
    if len(reason.strip())<5: raise ValueError('Record a reason for reassignment.')
    from .service import Hospital
    today=Hospital(path).today()
    with connection(path,write=True) as c:
        a=c.execute('SELECT * FROM appointments WHERE appointment_id=?',(appointment_id,)).fetchone()
        if not a or a['status']!='CONFIRMED' or a['urgency']<=2 or a['revision']!=revision or a['service_date']<today:
            raise Conflict('Only current, unstarted routine confirmations can be reassigned. Refresh the booking.')
        target=c.execute('''SELECT s.* FROM sessions s JOIN doctors d USING(doctor_id)
            WHERE s.doctor_id=? AND s.service_date=? AND s.enabled=1 AND d.specialty=?''',(doctor_id,a['service_date'],a['specialty'])).fetchone()
        load=c.execute("SELECT COUNT(*) FROM appointments WHERE doctor_id=? AND service_date=? AND status IN ('CONFIRMED','CHECKED_IN','FULFILLED')",(doctor_id,a['service_date'])).fetchone()[0]
        if not target or doctor_id==a['doctor_id'] or load>=target['capacity']:
            raise Conflict('Selected doctor is unavailable or full. Refresh the booking.')
        c.execute('UPDATE appointments SET doctor_id=?,revision=revision+1,attendance_confirmed=0 WHERE appointment_id=?',(doctor_id,appointment_id))
        from .access import actor
        marker=actor.set(user['user_id'])
        try:
            emit(c,'APPOINTMENT_REASSIGNED',appointment_id,reason=reason.strip(),old_doctor=a['doctor_id'],new_doctor=doctor_id)
            emit(c,'CAPACITY_CHANGED',str(a['doctor_id']),date=a['service_date'],specialty=a['specialty'])
        finally: actor.reset(marker)
    process_events(path)


def operational_snapshot(path,token,now=None):
    require_user(path,token,{'admin','physician','nurse','front_desk'})
    initialize(path)
    now=now or dt.datetime.now(dt.timezone.utc)
    date=now.astimezone(ZoneInfo('Africa/Lagos')).date().isoformat()
    with connection(path) as c:
        bookings=[dict(r) for r in c.execute('SELECT status,COUNT(*) AS count FROM appointments WHERE service_date=? GROUP BY status',(date,))]
        workload=[dict(r) for r in c.execute('''SELECT d.name,d.specialty,s.capacity,s.enabled,
            (SELECT COUNT(*) FROM appointments a WHERE a.doctor_id=d.doctor_id AND a.service_date=s.service_date
             AND a.status IN ('CONFIRMED','CHECKED_IN','FULFILLED')) AS allocated
            FROM sessions s JOIN doctors d USING(doctor_id) WHERE s.service_date=?''',(date,))]
        alerts=[]
        for a in c.execute('''SELECT a.appointment_id,p.name FROM appointments a JOIN patients p USING(patient_id)
            JOIN sessions s ON s.doctor_id=a.doctor_id AND s.service_date=a.service_date
            WHERE s.enabled=0 AND a.status='CONFIRMED' AND a.service_date>=?''',(date,)):
            alerts.append({'key':'unavailable:'+a['appointment_id'],'patient':a['name'],'record':a['appointment_id'],'reason':'Assigned doctor is unavailable. Review reassignment or rescheduling.','minutes':None})
        role=require_user(path,token,{'admin','physician','nurse','front_desk'})['role']
        waits=[]
        if role!='front_desk':
            for v in c.execute('''SELECT v.*,p.name FROM visits v JOIN appointments a USING(appointment_id)
                JOIN patients p USING(patient_id) WHERE v.state NOT IN ('COMPLETED','ADMITTED')'''):
                started=c.execute('SELECT 1 FROM consultations WHERE visit_id=? AND finished_at IS NULL',(v['visit_id'],)).fetchone()
                stamp=v['checked_in_at']
                last=c.execute("SELECT created_at FROM events WHERE entity_id=? AND kind='VISIT_TRANSITIONED' ORDER BY event_id DESC LIMIT 1",(v['visit_id'],)).fetchone()
                if last: stamp=last['created_at']
                # Vitals routing also changes state; avoid counting assessment time twice.
                vital=c.execute("SELECT created_at FROM events WHERE entity_id=? AND kind='VITALS_RECORDED' ORDER BY event_id DESC LIMIT 1",(v['visit_id'],)).fetchone()
                if vital and vital['created_at']>stamp: stamp=vital['created_at']
                minutes=max(0,(now-dt.datetime.fromisoformat(stamp).replace(tzinfo=dt.timezone.utc)).total_seconds()/60)
                waits.append({'State':v['state'],'Minutes in state':round(minutes,1)})
                limit=120 if v['state']=='DIAGNOSTICS' else 60
                if v['state']=='TRANSFER_REQUIRED' or (not started and minutes>=limit):
                    alerts.append({'key':v['visit_id']+':'+str(v['version']),'patient':v['name'],'record':v['visit_id'],
                                   'reason':('Transfer requires staff follow-up.' if v['state']=='TRANSFER_REQUIRED' else f'{v["state"]}: operational review threshold of {limit} minutes exceeded.'),'minutes':round(minutes)})
        reviewed={r['item_key']:dict(r) for r in c.execute('SELECT * FROM operational_reviews')}
        for alert in alerts:
            alert['review']=reviewed.get(alert['key'])
        return {'date':date,'bookings':bookings,'workload':workload,'alerts':alerts,'waits':waits}


def review_alert(path,token,key,note):
    user=require_user(path,token,{'admin','physician','nurse','front_desk'})
    if not note.strip(): raise ValueError('Record the follow-up action taken.')
    current=operational_snapshot(path,token)
    if key not in {a['key'] for a in current['alerts']}: raise Conflict('This alert is no longer active or accessible.')
    with connection(path,write=True) as c:
        c.execute('''INSERT INTO operational_reviews(item_key,note,user_id) VALUES (?,?,?)
            ON CONFLICT(item_key) DO UPDATE SET note=excluded.note,user_id=excluded.user_id,reviewed_at=CURRENT_TIMESTAMP''',(key,note.strip(),user['user_id']))
        audit(c,user,'REVIEW_OPERATIONAL_ALERT',key)
