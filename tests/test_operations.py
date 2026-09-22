import datetime as dt
import sqlite3
import pytest
from hospital.service import Hospital
from hospital.database import connection
from hospital.auth import initialize_auth,create_staff,login,authenticate,AccessDenied,change_own_password
from hospital.access import StaffHospital
from hospital.accounts import add_staff,manage_account,reset_account,invite_patient,accept_invitation
from hospital.portal import PatientPortal
from hospital.backup import snapshot,restore
from hospital.evaluation import generate,simulate
from hospital.errors import Conflict

PASSWORD='Synthetic-test-password-42'

@pytest.fixture
def setup(tmp_path):
    h=Hospital(str(tmp_path/'source.db'))
    initialize_auth(h.path)
    create_staff(h.path,'owner',PASSWORD,'admin')
    return h,login(h.path,'owner',PASSWORD)


def patient(h,admin,name='patient'):
    pid=h.create_patient(name,30,'Female',name+'@example.com')
    code=invite_patient(h.path,admin,pid)
    accept_invitation(h.path,code,name,PASSWORD)
    return pid,PatientPortal(h.path,login(h.path,name,PASSWORD))


def test_temporary_password_gate_and_account_revocation(setup):
    h,admin=setup
    uid=add_staff(h.path,admin,'nurse',PASSWORD,'nurse')
    token=login(h.path,'nurse',PASSWORD)
    with pytest.raises(AccessDenied): StaffHospital(h,token).records('SELECT * FROM patients')
    with pytest.raises(AccessDenied): add_staff(h.path,token,'other',PASSWORD,'admin')
    change_own_password(h.path,token,PASSWORD,'New-synthetic-password-42')
    with pytest.raises(AccessDenied): authenticate(h.path,token)
    token=login(h.path,'nurse','New-synthetic-password-42')
    assert StaffHospital(h,token).records('SELECT * FROM patients')==[]
    manage_account(h.path,admin,uid,'nurse',False)
    with pytest.raises(AccessDenied): authenticate(h.path,token)
    reset_account(h.path,admin,uid,PASSWORD)
    with pytest.raises(AccessDenied): login(h.path,'nurse',PASSWORD)
    with pytest.raises(ValueError): manage_account(h.path,admin,authenticate(h.path,admin)['user_id'],'nurse',True)


def test_invitation_single_use_and_expiry(setup):
    h,admin=setup
    pid=h.create_patient('Invite',20,'Male')
    code=invite_patient(h.path,admin,pid)
    accept_invitation(h.path,code,'invite',PASSWORD)
    with pytest.raises(AccessDenied): accept_invitation(h.path,code,'other',PASSWORD)
    pid=h.create_patient('Expired',20,'Male')
    code=invite_patient(h.path,admin,pid)
    with connection(h.path,write=True) as c: c.execute('UPDATE patient_invites SET expires_at=0')
    with pytest.raises(AccessDenied): accept_invitation(h.path,code,'expired',PASSWORD)


def test_patient_isolation_and_role_boundaries(setup):
    h,admin=setup
    first,p1=patient(h,admin,'first')
    second,p2=patient(h,admin,'second')
    h.set_session(1,h.today(),3)
    a=p1.book(h.today(),'General Practice')
    assert len(p1.appointments())==1 and p2.appointments()==[]
    assert p2.profile()['patient_id']==second
    for operation in (lambda:p2.cancel(a),lambda:p2.reschedule(a,0,h.today()),lambda:p2.consent(a,False),lambda:p2.confirm_attendance(a)):
        with pytest.raises(AccessDenied): operation()
    with pytest.raises(AccessDenied): StaffHospital(h,p1.token).clear_billing(a,'fake')
    with pytest.raises(AccessDenied): add_staff(h.path,p1.token,'injected',PASSWORD,'admin')
    with pytest.raises(ValueError): p1.book(h.today(),'Emergency / Trauma')
    with pytest.raises(ValueError): manage_account(h.path,admin,p1.user['user_id'],'admin',True)
    p1.confirm_attendance(a)
    assert p1.appointments()[0]['attendance_confirmed']==1
    tomorrow=(dt.date.fromisoformat(h.today())+dt.timedelta(days=1)).isoformat()
    h.set_session(1,tomorrow,3)
    p1.reschedule(a,0,tomorrow)
    assert p1.appointments()[0]['attendance_confirmed']==0


def test_email_verification_cooldown_binding_and_attempt_limit(setup):
    h,admin=setup
    pid,p=patient(h,admin)
    messages=[]
    sender=lambda recipient,body,key:messages.append((recipient,body,key))
    p.request_verification(sender)
    code=messages[0][1].splitlines()[1]
    with pytest.raises(ValueError): p.request_verification(sender)
    with pytest.raises(ValueError): p.verify_email('wrong')
    p.verify_email(code)
    assert p.profile()['verified_email']=='patient@example.com'
    with pytest.raises(ValueError): p.verify_email(code)
    p.request_verification(sender)
    code=messages[-1][1].splitlines()[1]
    h.update_email(pid,'patient@example.com','changed@example.com')
    with pytest.raises(ValueError): p.verify_email(code)
    assert not p.profile()['verified_email']
    with connection(h.path,write=True) as c: c.execute('UPDATE email_verifications SET requested_at=0')
    p.request_verification(sender)
    code=messages[-1][1].splitlines()[1]
    for _ in range(5):
        with pytest.raises(ValueError): p.verify_email('wrong')
    with pytest.raises(ValueError): p.verify_email(code)


def test_disabled_doctor_and_consultation_exclusivity(setup,monkeypatch):
    h,_=setup
    class Clock(dt.datetime):
        @classmethod
        def now(cls,tz=None): return cls(2026,9,19,12,0,tzinfo=tz)
    monkeypatch.setattr('hospital.scheduling.dt.datetime',Clock)
    h.set_session(1,h.today(),3)
    h.set_availability(1,h.today(),'09:00','17:00',False)
    pid=h.create_patient('Waiting',20,'Female')
    a=h.book(pid,h.today(),'General Practice')
    assert h.records('SELECT status FROM appointments')[0]['status']=='WAITLISTED'
    h.set_availability(1,h.today(),'09:00','17:00',True)
    h.clear_billing(a,'TEST')
    v=h.check_in(a)
    h.record_vitals(v,0,120,37,75,98,16)
    h.add_break(1,h.today(),'11:00','13:00','Lunch')
    with pytest.raises(Conflict): h.start_consultation(v,1)
    h.remove_break(h.records('SELECT break_id FROM doctor_breaks')[0]['break_id'])
    h.start_consultation(v,1)
    with pytest.raises(Conflict): h.start_consultation(v,2)
    pid=h.create_patient('Next',30,'Male')
    a=h.book(pid,h.today(),'General Practice')
    h.clear_billing(a,'TEST2')
    v2=h.check_in(a)
    h.record_vitals(v2,0,120,37,75,98,16)
    with pytest.raises(Conflict): h.start_consultation(v2,1)
    h.transition(v,2,'COMPLETED','Finished')
    assert h.records('SELECT finished_at FROM consultations')[0]['finished_at'] is not None
    h.start_consultation(v2,1)


def test_backup_restores_records_but_not_sessions_and_never_overwrites(setup,tmp_path):
    h,admin=setup
    patient(h,admin)
    backup=snapshot(h.path,tmp_path/'backup.db')
    restored=restore(backup,tmp_path/'restored.db')
    with sqlite3.connect(restored) as c:
        assert c.execute('SELECT COUNT(*) FROM patients').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM staff_sessions').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM patient_invites').fetchone()[0]==0
    with pytest.raises(FileExistsError): snapshot(h.path,backup)
    assert authenticate(h.path,admin)['role']=='admin'
    assert login(str(restored),'owner',PASSWORD)


@pytest.mark.parametrize('policy',['FCFS','Urgency with aging'])
def test_simulation_preserves_arrival_and_server_constraints(policy):
    arrivals=generate(42,200)
    rows=simulate(arrivals,policy,3)
    assert rows==simulate(generate(42,200),policy,3)
    assert len(rows)==200 and all(r['wait']>=0 for r in rows)
    for doctor in range(3):
        queue=sorted((r for r in rows if r['doctor']==doctor),key=lambda r:r['start'])
        for previous,current in zip(queue,queue[1:]):
            assert current['start']>=previous['start']+previous['duration']
    assert {r['id'] for r in rows}==set(range(200))
