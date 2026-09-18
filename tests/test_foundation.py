import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import pytest
from hospital.service import Hospital, Conflict
from hospital.database import connection
from hospital.agents import process_events, WaitlistAgent
from hospital.import_legacy import import_snapshot


@pytest.fixture
def h(tmp_path):
    return Hospital(str(tmp_path/'next.db'),today=lambda:'2026-10-01')


def patient(h,name='Test Patient'):
    return h.create_patient(name,30,'Female','test@example.com')


def booked(h):
    h.set_session(1,'2026-10-01',1)
    a=h.book(patient(h),'2026-10-01','General Practice')
    h.clear_billing(a,'Test clearance')
    return a


def test_patient_identity_is_not_name_matching(h):
    p1,p2=patient(h),patient(h)
    assert p1!=p2
    h.set_session(1,'2026-10-01',2)
    h.set_session(1,'2026-10-02',2)
    for date in ('2026-10-01','2026-10-02'):
        h.book(p1,date,'General Practice')
    assert len(h.records('SELECT * FROM appointments WHERE patient_id=?',(p1,)))==2


def test_booking_and_checkin_are_separate_and_idempotent(h):
    a=booked(h)
    assert not h.records('SELECT * FROM visits')
    v=h.check_in(a)
    with pytest.raises(Conflict): h.check_in(a)
    assert len(h.records('SELECT * FROM visits'))==1
    assert h.records('SELECT * FROM visits')[0]['visit_id']==v


def test_cannot_check_in_future_or_waitlisted(h):
    h.set_session(1,'2026-10-02',1)
    a=h.book(patient(h),'2026-10-02','General Practice')
    with pytest.raises(Conflict): h.check_in(a)
    b=h.book(patient(h),'2026-10-01','General Practice')
    with pytest.raises(Conflict): h.check_in(b)


def test_stale_and_terminal_transitions_rejected(h):
    v=h.check_in(booked(h))
    h.record_vitals(v,0,120,37,75,98,16)
    with pytest.raises(Conflict): h.transition(v,0,'DIAGNOSTICS')
    h.transition(v,1,'COMPLETED')
    with pytest.raises(Conflict): h.transition(v,2,'DIAGNOSTICS')
    assert 'Vitals recorded' in h.records('SELECT * FROM visits')[0]['notes']


def test_cancel_fills_capacity_and_event_replay_does_not_duplicate(h):
    a=booked(h)
    low=h.book(patient(h),'2026-10-01','General Practice',5)
    high=h.book(patient(h),'2026-10-01','General Practice',3)
    h.close_booking(a,'CANCELLED')
    statuses={r['appointment_id']:r['status'] for r in h.records('SELECT * FROM appointments')}
    assert statuses[high]=='CONFIRMED' and statuses[low]=='WAITLISTED'
    before=h.records('SELECT * FROM decisions')
    assert process_events(h.path)==0
    assert h.records('SELECT * FROM decisions')==before
    with pytest.raises(Conflict): h.close_booking(a,'CANCELLED')


def test_completion_does_not_release_booking_allocation(h):
    a=booked(h)
    waiting=h.book(patient(h),'2026-10-01','General Practice')
    v=h.check_in(a)
    h.record_vitals(v,0,120,37,75,98,16)
    h.transition(v,1,'COMPLETED')
    assert h.records('SELECT status FROM appointments WHERE appointment_id=?',(waiting,))[0]['status']=='WAITLISTED'
    with pytest.raises(Conflict): h.set_session(1,'2026-10-01',0)


def test_capacity_checks_are_serialized(h):
    h.set_session(1,'2026-10-01',1)
    people=[patient(h) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda p:h.book(p,'2026-10-01','General Practice'),people))
    assert len(h.records("SELECT * FROM appointments WHERE status='CONFIRMED'"))==1
    assert len(h.records("SELECT * FROM appointments WHERE status='WAITLISTED'"))==5


def test_background_event_failure_can_recover_after_restart(h):
    a=booked(h)
    waiting=h.book(patient(h),'2026-10-01','General Practice')
    with patch.object(WaitlistAgent,'handle',side_effect=RuntimeError('failure')):
        h.close_booking(a,'CANCELLED')
    assert h.records('SELECT * FROM events WHERE processed_at IS NULL')
    restarted=Hospital(h.path,today=h.today)
    process_events(restarted.path)
    assert h.records('SELECT status FROM appointments WHERE appointment_id=?',(waiting,))[0]['status']=='CONFIRMED'
    assert not h.records('SELECT * FROM events WHERE processed_at IS NULL')


def test_emergency_future_intake_rejected(h):
    with pytest.raises(ValueError): h.book(patient(h),'2026-10-02','Emergency / Trauma',1)
    with pytest.raises(Conflict): h.book(patient(h),'2026-10-01','Emergency / Trauma',1)
    assert not h.records('SELECT * FROM appointments')


def test_legacy_snapshot_import_is_atomic_idempotent_and_read_only(h,tmp_path):
    source=tmp_path/'old.db'
    with sqlite3.connect(source) as conn:
        conn.executescript('''CREATE TABLE doctors(doc_id INTEGER,name TEXT,specialty TEXT);
            INSERT INTO doctors VALUES(1,'Dr. Smith','General Practice');
            CREATE TABLE appointments(booking_date TEXT,doc_id INTEGER,patient_name TEXT,age INTEGER,gender TEXT,status TEXT,location TEXT,triage_level INTEGER,notes TEXT);
            INSERT INTO appointments VALUES('2026-10-01',1,'Original',30,'Male','WAITING','Nurses Station',4,'Keep me');
            CREATE TABLE waitlist(booking_date TEXT,patient_name TEXT,age INTEGER,gender TEXT,triage_level INTEGER,specialty TEXT);
            INSERT INTO waitlist VALUES('2026-10-01','Original',30,'Male',4,'General Practice');''')
    before=source.read_bytes()
    assert import_snapshot(h,source)==2
    assert import_snapshot(h,source)==0
    assert source.read_bytes()==before
    assert len(h.records('SELECT * FROM patients'))==2
    assert 'Keep me' in h.records('SELECT * FROM legacy_records')[0]['record_json']
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE appointments SET status='UNKNOWN'")
    with pytest.raises(ValueError): import_snapshot(h,source)
    assert len(h.records('SELECT * FROM patients'))==2


def test_existing_booking_cannot_be_duplicated(h):
    h.set_session(1,'2026-10-01',3)
    p=patient(h)
    h.book(p,'2026-10-01','General Practice')
    with pytest.raises(Conflict): h.book(p,'2026-10-01','General Practice')


def test_past_cancellation_does_not_confirm_past_waitlist(h):
    a=booked(h)
    w=h.book(patient(h),'2026-10-01','General Practice')
    h.today=lambda:'2026-10-02'
    h.close_booking(a,'MISSED')
    assert h.records('SELECT status FROM appointments WHERE appointment_id=?',(w,))[0]['status']=='WAITLISTED'


def test_legacy_database_cannot_be_opened_as_new_schema(tmp_path):
    source=tmp_path/'legacy.db'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE appointments(booking_date TEXT)')
    with pytest.raises(ValueError): Hospital(str(source))
