import datetime as dt
from concurrent.futures import ThreadPoolExecutor
import pytest
from hospital.service import Hospital,Conflict
from hospital.notifications import dispatch


@pytest.fixture
def h(tmp_path):
    return Hospital(str(tmp_path/'schedule.db'),today=lambda:'2026-10-01')


def book(h,date='2026-10-02',consent=False):
    p=h.create_patient('Schedule test',30,'Female','test@example.com',confirm_distinct=True)
    return h.book(p,date,'General Practice',5,consent)


def row(h,a):
    return h.records('SELECT * FROM appointments WHERE appointment_id=?',(a,))[0]


def test_reschedule_releases_old_place_preserves_identity_and_billing(h):
    h.set_session(1,'2026-10-02',1)
    h.set_session(1,'2026-10-03',1)
    a=book(h,consent=True)
    h.clear_billing(a,'Paid record')
    waiting=book(h)
    original=row(h,a)
    assert h.reschedule(a,0,'2026-10-03')=='CONFIRMED'
    after=row(h,a)
    assert after['patient_id']==original['patient_id']
    assert after['billing_reference']=='Paid record' and after['reminder_opt_in']==1
    assert after['revision']==1 and row(h,waiting)['status']=='CONFIRMED'
    assert not h.records('SELECT * FROM visits')
    with pytest.raises(Conflict): h.reschedule(a,0,'2026-10-04')


def test_full_target_requires_explicit_waitlist_acceptance(h):
    h.set_session(1,'2026-10-02',1)
    a=book(h)
    original=row(h,a)
    with pytest.raises(Conflict): h.reschedule(a,0,'2026-10-03')
    assert row(h,a)==original
    assert h.reschedule(a,0,'2026-10-03',True)=='WAITLISTED'
    assert row(h,a)['doctor_id'] is None


def test_concurrent_reschedules_cannot_overfill(h):
    h.set_session(1,'2026-10-02',2)
    h.set_session(1,'2026-10-03',1)
    bookings=[book(h),book(h)]
    def change(a):
        try: return h.reschedule(a,0,'2026-10-03')
        except Conflict: return 'REJECTED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change,bookings))==['CONFIRMED','REJECTED']
    assert sorted(row(h,a)['service_date'] for a in bookings)==['2026-10-02','2026-10-03']


def test_started_emergency_duplicate_and_past_changes_rejected(h):
    h.set_session(1,'2026-10-02',2)
    h.set_session(1,'2026-10-03',2)
    a=book(h)
    h.book(row(h,a)['patient_id'],'2026-10-03','General Practice')
    with pytest.raises(Conflict): h.reschedule(a,0,'2026-10-03')
    with pytest.raises(ValueError): h.reschedule(a,0,'2026-09-30')
    with pytest.raises(ValueError): h.reschedule(a,0,'2026-10-02')
    h.set_session(7,h.today(),1)
    p=h.create_patient('Emergency',30,'Female')
    ed=h.book(p,h.today(),'Emergency / Trauma',2)
    with pytest.raises(Conflict): h.reschedule(ed,0,'2026-10-03')
    h.set_session(1,h.today(),1)
    today=book(h,h.today())
    h.clear_billing(today,'Test')
    h.check_in(today)
    with pytest.raises(Conflict): h.reschedule(today,0,'2026-10-03')


def test_reminders_follow_new_date_and_revision_without_retries(h):
    h.set_session(1,'2026-10-02',1)
    h.set_session(1,'2026-10-03',1)
    a=book(h,consent=True)
    h.clear_billing(a,'Test')
    now=dt.datetime(2026,10,1,9)
    calls=[]
    send=lambda *args:(calls.append(args) or 'receipt')
    assert dispatch(h.path,now=now,sender=send)==1
    h.reschedule(a,0,'2026-10-03')
    assert dispatch(h.path,now=now,sender=send)==0
    h.reschedule(a,1,'2026-10-02')
    assert dispatch(h.path,now=now,sender=send)==1
    assert calls[0][2]!=calls[1][2]
    assert dispatch(h.path,now=now,sender=send)==0


def test_move_to_today_clears_day_before_consent(h):
    h.set_session(1,'2026-10-02',1)
    h.set_session(1,h.today(),1)
    a=book(h,consent=True)
    h.reschedule(a,0,h.today())
    assert row(h,a)['reminder_opt_in']==0
