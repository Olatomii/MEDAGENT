import datetime as dt
import time
from zoneinfo import ZoneInfo
from .database import connection
from .agents import emit,process_events
from .errors import Conflict


def time_range(start,end):
    start,end=dt.time.fromisoformat(start),dt.time.fromisoformat(end)
    if start>=end or start.second or end.second:
        raise ValueError('Use a same-day start and end time, with end after start.')
    return start.strftime('%H:%M'),end.strftime('%H:%M')


def finish_consultation(conn,visit_id):
    conn.execute('UPDATE consultations SET finished_at=? WHERE visit_id=? AND finished_at IS NULL',(time.time(),visit_id))


class Scheduling:
    def set_availability(self,doctor_id,date,start,end,enabled):
        start,end=time_range(start,end)
        with connection(self.path,write=True) as conn:
            session=conn.execute('SELECT * FROM sessions WHERE doctor_id=? AND service_date=?',(doctor_id,date)).fetchone()
            if session is None:
                raise ValueError('Configure a session and capacity first.')
            conn.execute('UPDATE sessions SET start_time=?,end_time=?,enabled=? WHERE doctor_id=? AND service_date=?',
                         (start,end,int(enabled),doctor_id,date))
            spec=conn.execute('SELECT specialty FROM doctors WHERE doctor_id=?',(doctor_id,)).fetchone()[0]
            emit(conn,'CAPACITY_CHANGED',str(doctor_id),date=date,specialty=spec,allow_promotion=date>=self.today())
            emit(conn,'AVAILABILITY_CHANGED',str(doctor_id),reason=f'Staff set {date} availability {start}–{end}; enabled={bool(enabled)}. Existing reservations retained for review.')
        process_events(self.path)

    def add_break(self,doctor_id,date,start,end,reason):
        start,end=time_range(start,end)
        if not reason.strip():
            raise ValueError('Enter a reason for the break or temporary unavailability.')
        with connection(self.path,write=True) as conn:
            session=conn.execute('SELECT * FROM sessions WHERE doctor_id=? AND service_date=?',(doctor_id,date)).fetchone()
            if session is None or start<session['start_time'] or end>session['end_time']:
                raise ValueError('Choose an interval within the configured session.')
            conn.execute('INSERT INTO doctor_breaks(doctor_id,service_date,start_time,end_time,reason) VALUES (?,?,?,?,?)',
                         (doctor_id,date,start,end,reason.strip()))
            emit(conn,'AVAILABILITY_CHANGED',str(doctor_id),reason=f'Staff recorded a break on {date}, {start}–{end}.')
        process_events(self.path)

    def remove_break(self,break_id):
        with connection(self.path,write=True) as conn:
            row=conn.execute('SELECT * FROM doctor_breaks WHERE break_id=?',(break_id,)).fetchone()
            if row is None:
                raise Conflict('Break already removed.')
            conn.execute('DELETE FROM doctor_breaks WHERE break_id=?',(break_id,))
            emit(conn,'AVAILABILITY_CHANGED',str(row['doctor_id']),reason='Staff removed break '+str(break_id)+'.')
        process_events(self.path)

    def start_consultation(self,visit_id,version):
        now=dt.datetime.now(ZoneInfo('Africa/Lagos'))
        clock=now.strftime('%H:%M')
        with connection(self.path,write=True) as conn:
            visit=conn.execute('SELECT * FROM visits WHERE visit_id=?',(visit_id,)).fetchone()
            if visit is None or visit['version']!=version or visit['state']!='CONSULTATION':
                raise Conflict('Visit changed or is not awaiting consultation.')
            doctor_id=visit['assigned_doctor_id']
            session=conn.execute('SELECT * FROM sessions WHERE doctor_id=? AND service_date=?',(doctor_id,self.today())).fetchone()
            if session is None or not session['enabled'] or not session['start_time']<=clock<session['end_time']:
                raise Conflict('Doctor is outside the staffed session or marked unavailable.')
            if conn.execute('SELECT 1 FROM doctor_breaks WHERE doctor_id=? AND service_date=? AND start_time<=? AND end_time>?',
                            (doctor_id,self.today(),clock,clock)).fetchone():
                raise Conflict('Doctor is on a recorded break.')
            if conn.execute('SELECT 1 FROM consultations WHERE (doctor_id=? OR visit_id=?) AND finished_at IS NULL',(doctor_id,visit_id)).fetchone():
                raise Conflict('Doctor or visit already has an active consultation.')
            conn.execute('INSERT INTO consultations(visit_id,doctor_id,started_at) VALUES (?,?,?)',(visit_id,doctor_id,time.time()))
            conn.execute('UPDATE visits SET version=version+1 WHERE visit_id=?',(visit_id,))
            emit(conn,'CONSULTATION_STARTED',visit_id,reason='Staff started consultation with an available doctor. No fixed duration assigned.')
        process_events(self.path)
