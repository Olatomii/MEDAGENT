import datetime as dt
from concurrent.futures import ThreadPoolExecutor
import pytest
from hospital.service import Hospital,Conflict
from hospital.database import connection
from hospital.notifications import dispatch,suggested_email


@pytest.fixture
def h(tmp_path):
    return Hospital(str(tmp_path/'care.db'),today=lambda:'2026-10-01')


def booking(h,date='2026-10-01',clear=True,consent=False):
    h.set_session(1,date,20)
    p=h.create_patient('Synthetic patient',30,'Female','patient@example.com',confirm_distinct=True)
    a=h.book(p,date,'General Practice',5,consent)
    if clear:
        h.clear_billing(a,'Synthetic clearance')
    return a


def consult(h):
    v=h.check_in(booking(h))
    h.record_vitals(v,0,120,37,75,98,16)
    return v


def test_billing_gate_and_emergency_exemption(h):
    a=booking(h,clear=False)
    with pytest.raises(Conflict): h.check_in(a)
    with pytest.raises(ValueError): h.clear_billing(a,'')
    h.clear_billing(a,'Desk record 1')
    assert h.check_in(a)
    h.set_session(7,h.today(),1)
    p=h.create_patient('Emergency',20,'Male')
    emergency=h.book(p,h.today(),'Emergency / Trauma',2)
    assert h.records('SELECT billing_status FROM appointments WHERE appointment_id=?',(emergency,))[0]['billing_status']=='EXEMPT'
    assert h.check_in(emergency)


def test_normal_vitals_diagnostics_and_pharmacy_journey(h):
    v=consult(h)
    h.transition(v,1,'DIAGNOSTICS','CBC requested')
    with pytest.raises(ValueError): h.transition(v,2,'CONSULTATION','')
    h.transition(v,2,'CONSULTATION','Synthetic results recorded')
    h.transition(v,3,'PHARMACY','Synthetic prescription')
    h.transition(v,4,'COMPLETED','Dispensing recorded')
    row=h.records('SELECT * FROM visits')[0]
    assert row['state']=='COMPLETED' and row['urgency']==5
    assert len(h.records('SELECT * FROM vitals'))==1
    with pytest.raises(Conflict): h.transition(v,4,'COMPLETED')
    with pytest.raises(Conflict): h.transition(v,5,'DIAGNOSTICS')


def test_critical_vitals_reserve_emergency_or_preserve_transfer(h):
    h.set_session(7,h.today(),1)
    v1=h.check_in(booking(h))
    v2=h.check_in(booking(h))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda v:h.record_vitals(v,0,190,37,75,98,16),(v1,v2)))
    visits=h.records('SELECT * FROM visits')
    assert sorted(v['state'] for v in visits)==['CONSULTATION','TRANSFER_REQUIRED']
    assert all(v['urgency']==2 for v in visits)
    assert len(h.records('SELECT * FROM vitals'))==2
    # The original appointment department remains part of the booking history.
    assert all(a['specialty']=='General Practice' for a in h.records('SELECT * FROM appointments'))


def test_emergency_place_reusable_after_completed_visit(h):
    h.set_session(7,h.today(),1)
    p=h.create_patient('First ED',30,'Male')
    a=h.book(p,h.today(),'Emergency / Trauma',2)
    v=h.check_in(a)
    h.transition(v,0,'COMPLETED')
    h.set_session(7,h.today(),1)
    another=h.book(h.create_patient('Next ED',30,'Female'),h.today(),'Emergency / Trauma',2)
    assert h.check_in(another)


def test_vital_replay_and_bad_readings_rejected(h):
    v=h.check_in(booking(h))
    with pytest.raises(ValueError): h.record_vitals(v,0,120,float('nan'),75,98,16)
    with pytest.raises(Conflict): h.transition(v,0,'CONSULTATION')
    assert not h.records('SELECT * FROM vitals')
    h.record_vitals(v,0,120,37,75,98,16)
    with pytest.raises(Conflict): h.record_vitals(v,0,120,37,75,98,16)
    assert len(h.records('SELECT * FROM vitals'))==1


def test_ward_capacity_competing_admissions_and_discharge(h):
    v1,v2=consult(h),consult(h)
    with pytest.raises(Conflict): h.transition(v1,1,'ADMITTED')
    with pytest.raises(Conflict): h.admit(v1,1,2)
    h.set_ward_capacity(2,1)
    def admit(v):
        try:
            h.admit(v,1,2)
            return True
        except Conflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(admit,(v1,v2)))==1
    admitted=h.records("SELECT * FROM visits WHERE state='ADMITTED'")[0]
    with pytest.raises(Conflict): h.set_ward_capacity(2,0)
    h.transition(admitted['visit_id'],2,'COMPLETED','Discharge recorded')
    other=v1 if admitted['visit_id']==v2 else v2
    h.admit(other,1,2)
    assert len(h.records("SELECT * FROM visits WHERE state='ADMITTED'"))==1


NOW=dt.datetime(2026,10,1,9,tzinfo=dt.timezone(dt.timedelta(hours=1)))


def test_reminder_consent_clearance_date_and_duplicates(h):
    eligible=booking(h,'2026-10-02',consent=True)
    booking(h,'2026-10-02',consent=False)
    booking(h,'2026-10-02',clear=False,consent=True)
    cancelled=booking(h,'2026-10-02',consent=True)
    h.close_booking(cancelled,'CANCELLED')
    calls=[]
    sender=lambda *args:(calls.append(args) or 'provider-id')
    assert dispatch(h.path,now=NOW.replace(hour=8),sender=sender)==0
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _:dispatch(h.path,now=NOW,sender=sender),range(2)))==1
    assert len(calls)==1
    assert dispatch(h.path,now=NOW,sender=sender)==0
    assert h.records('SELECT * FROM notifications')[0]['appointment_id']==eligible


def test_reminder_ambiguous_failure_not_retried(h):
    booking(h,'2026-10-02',consent=True)
    def timeout(*args): raise TimeoutError('Sensitive provider detail')
    assert dispatch(h.path,now=NOW,sender=timeout)==0
    row=h.records('SELECT * FROM notifications')[0]
    assert row['status']=='REVIEW_REQUIRED' and row['error_code']=='TimeoutError'
    def forbidden(*args): pytest.fail('Ambiguous send retried')
    assert dispatch(h.path,now=NOW,sender=forbidden)==0


def test_reminder_disabled_without_v2_activation(h,monkeypatch):
    booking(h,'2026-10-02',consent=True)
    monkeypatch.delenv('MEDAGENT_V2_REMINDERS_ENABLED',raising=False)
    monkeypatch.setenv('REMINDERS_ENABLED','true')
    assert dispatch(h.path,now=NOW)==0
    assert not h.records('SELECT * FROM notifications')


def test_contact_change_revokes_consent_and_stale_edit_is_rejected(h):
    a=booking(h,'2026-10-02',consent=True)
    p=h.records('SELECT * FROM patients')[0]
    h.update_email(p['patient_id'],p['email'],'new@example.com')
    with pytest.raises(Conflict): h.update_email(p['patient_id'],p['email'],'stale@example.com')
    assert h.records('SELECT reminder_opt_in FROM appointments')[0]['reminder_opt_in']==0
    h.reminder_preference(a,True)
    calls=[]
    assert dispatch(h.path,now=NOW,sender=lambda *args:(calls.append(args) or 'id'))==1
    assert calls[0][0]=='new@example.com'


def test_typo_hint_does_not_rewrite_address():
    assert suggested_email('solomondemilade17@gmail.co')=='solomondemilade17@gmail.com'
    assert suggested_email('person@example.co') is None


def test_additive_migration_retains_data_and_is_repeatable(h):
    v=consult(h)
    Hospital(h.path,today=h.today)
    Hospital(h.path,today=h.today)
    assert h.records('SELECT visit_id FROM visits')[0]['visit_id']==v
    assert len(h.records('SELECT * FROM vitals'))==1
