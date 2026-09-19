import datetime as dt
from concurrent.futures import ThreadPoolExecutor
import pytest
from hospital.service import Hospital
from hospital.auth import initialize_auth,create_staff,login,AccessDenied
from hospital.database import connection
from hospital.portal import PatientPortal
from hospital import experience as x
from hospital.errors import Conflict

PW='Synthetic-test-password-42'
@pytest.fixture
def setup(tmp_path):
    h=Hospital(str(tmp_path/'experience.db'))
    initialize_auth(h.path)
    create_staff(h.path,'admin',PW,'admin')
    return h,login(h.path,'admin',PW)


def test_registration_never_claims_matching_identity(setup):
    h,admin=setup
    original=h.create_patient('Shared name',30,'Female','same@example.com')
    pid=x.register(h.path,'Shared name',30,'Female','same@example.com','newpatient',PW)
    assert pid!=original and pid.startswith('P-')
    p=PatientPortal(h.path,login(h.path,'newpatient',PW))
    assert p.profile()['patient_id']==pid and not p.appointments()
    with pytest.raises(ValueError,match='identity review'): p.book(h.today(),'General Practice')
    assert len(x.duplicates(h.path,admin))==1
    with pytest.raises(AccessDenied): x.resolve_duplicate(h.path,p.token,pid,original,'Verified different people')
    x.resolve_duplicate(h.path,admin,pid,original,'Verified different people through staff identity checks')
    p.book(h.today(),'General Practice')
    assert len(p.appointments())==1


def test_staff_duplicate_confirmation_and_registration_rollback(setup):
    h,admin=setup
    h.create_patient('Person',30,'Male','shared@example.com')
    with pytest.raises(Conflict): h.create_patient('Other',20,'Female','shared@example.com')
    h.create_patient('Other',20,'Female','shared@example.com',True)
    before=len(h.records('SELECT * FROM patients'))
    with pytest.raises(ValueError): x.register(h.path,'New',20,'Male','valid@example.com','admin',PW)
    assert len(h.records('SELECT * FROM patients'))==before


def test_reassignment_capacity_concurrency_and_patient_updates(setup):
    h,admin=setup
    # Doctors 1,2,3 share General Practice.
    docs=h.records("SELECT doctor_id FROM doctors WHERE specialty='General Practice'")
    first,last=docs[0]['doctor_id'],docs[1]['doctor_id']
    h.set_session(first,h.today(),2)
    ids=[]
    for i in range(2):
        pid=x.register(h.path,'Patient '+str(i),20,'Female',f'p{i}@example.com','patient'+str(i),PW)
        ids.append(h.book(pid,h.today(),'General Practice'))
    h.set_session(last,h.today(),1)
    def move(a):
        try: x.reassign(h.path,admin,a,0,last,'Original doctor absent');return True
        except Conflict:return False
    with ThreadPoolExecutor(2) as pool: assert sum(pool.map(move,ids))==1
    assert h.records('SELECT COUNT(*) AS n FROM appointments WHERE doctor_id=?',(last,))[0]['n']==1
    for i,a in enumerate(ids):
        portal=PatientPortal(h.path,login(h.path,'patient'+str(i),PW))
        assert all(r['Appointment']==a for r in portal.updates())
        moved=h.records('SELECT revision FROM appointments WHERE appointment_id=?',(a,))[0]['revision']
        assert any('doctor changed' in r['Update'] for r in portal.updates())==bool(moved)
        with pytest.raises(AccessDenied): x.reassign(h.path,portal.token,a,moved,first,'Unauthorized')


def test_alerts_role_boundaries_and_followup_preserves_flags(setup):
    h,admin=setup
    h.set_session(1,h.today(),2)
    p=h.create_patient('Visit',20,'Female')
    a=h.book(p,h.today(),'General Practice')
    h.clear_billing(a,'TEST');v=h.check_in(a)
    old=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
    with connection(h.path,write=True) as c:c.execute('UPDATE visits SET checked_in_at=? WHERE visit_id=?',(old,v))
    data=x.operational_snapshot(h.path,admin)
    assert len(data['alerts'])==1
    key=data['alerts'][0]['key']
    x.review_alert(h.path,admin,key,'Nurse contacted and assessment requested')
    assert x.operational_snapshot(h.path,admin)['alerts'][0]['review']
    create_staff(h.path,'desk',PW,'front_desk');desk=login(h.path,'desk',PW)
    assert not x.operational_snapshot(h.path,desk)['alerts']
    with pytest.raises(Conflict):x.review_alert(h.path,desk,key,'Attempt forbidden clinical followup')
    p=x.register(h.path,'Patient',20,'Female','new@example.com','patient',PW)
    with pytest.raises(AccessDenied):x.operational_snapshot(h.path,login(h.path,'patient',PW))
