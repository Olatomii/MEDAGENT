import time
import pytest
from hospital.service import Hospital
from hospital.auth import initialize_auth,create_staff,login,authenticate,logout,disable_staff,reset_password,AccessDenied,SESSION_SECONDS
from hospital.access import StaffHospital

PASSWORD='Synthetic-test-password-42'


@pytest.fixture
def core(tmp_path):
    h=Hospital(str(tmp_path/'staff.db'))
    initialize_auth(h.path)
    return h


def staff(core,role):
    create_staff(core.path,role,PASSWORD,role)
    return StaffHospital(core,login(core.path,role,PASSWORD))


def test_no_default_account_and_no_plaintext_credentials(core):
    assert not core.records('SELECT * FROM staff_users')
    user=staff(core,'admin')
    row=core.records('SELECT * FROM staff_users')[0]
    assert row['password_hash']!=PASSWORD and len(row['salt'])==32
    session=core.records('SELECT * FROM staff_sessions')[0]
    assert session['token_hash']!=user._token


def test_session_expiry_logout_and_revocation(core):
    create_staff(core.path,'test-user',PASSWORD,'nurse')
    now=time.time()
    token=login(core.path,'test-user',PASSWORD,now=now)
    assert authenticate(core.path,token,now+1)['role']=='nurse'
    with pytest.raises(AccessDenied): authenticate(core.path,token,now+SESSION_SECONDS)
    logout(core.path,token)
    with pytest.raises(AccessDenied): authenticate(core.path,token)
    token=login(core.path,'test-user',PASSWORD)
    disable_staff(core.path,'test-user')
    with pytest.raises(AccessDenied): authenticate(core.path,token)


def test_failed_logins_are_throttled_and_recover_after_cooldown(core):
    create_staff(core.path,'tester',PASSWORD,'nurse')
    for _ in range(5):
        with pytest.raises(AccessDenied): login(core.path,'tester','incorrect',now=100)
    with pytest.raises(AccessDenied): login(core.path,'tester',PASSWORD,now=101)
    assert login(core.path,'tester',PASSWORD,now=1001)


def test_permissions_block_commands_even_without_ui(core):
    nurse=staff(core,'nurse')
    with pytest.raises(AccessDenied): nurse.create_patient('Forbidden',30,'Female')
    with pytest.raises(AccessDenied): nurse.set_session(1,core.today(),10)
    with pytest.raises(AccessDenied): nurse.authorize_page('Doctor sessions')
    assert not core.records('SELECT * FROM patients')


def test_front_desk_reads_cannot_access_clinical_or_auth_tables(core):
    desk=staff(core,'front_desk')
    assert desk.records('SELECT * FROM patients')==[]
    for query in ('SELECT * FROM visits','SELECT * FROM staff_users','SELECT * FROM staff_sessions',
                  'DELETE FROM patients',"SELECT * FROM patients UNION SELECT * FROM staff_users"):
        with pytest.raises(AccessDenied): desk.records(query)


def test_actor_is_written_with_business_event(core):
    desk=staff(core,'front_desk')
    p=desk.create_patient('Audited person',30,'Female')
    event=core.records('SELECT * FROM events WHERE entity_id=?',(p,))[0]
    assert event['actor_id']==desk.user['user_id']
    core.create_patient('Maintenance',30,'Male')
    assert core.records('SELECT * FROM events ORDER BY event_id DESC LIMIT 1')[0]['actor_id'] is None


def test_pharmacy_cannot_act_on_consultation(core):
    core.set_session(1,core.today(),1)
    p=core.create_patient('Patient',30,'Female')
    a=core.book(p,core.today(),'General Practice')
    core.clear_billing(a,'TEST')
    v=core.check_in(a)
    core.record_vitals(v,0,120,37,75,98,16)
    pharmacy=staff(core,'pharmacy')
    with pytest.raises(AccessDenied): pharmacy.transition(v,1,'COMPLETED')
    core.transition(v,1,'PHARMACY','Prescription')
    pharmacy.transition(v,2,'COMPLETED')
    assert core.records('SELECT state FROM visits')[0]['state']=='COMPLETED'


def test_last_admin_cannot_be_disabled(core):
    staff(core,'admin')
    with pytest.raises(ValueError): disable_staff(core.path,'admin')


def test_revoked_session_cannot_write(core):
    desk=staff(core,'front_desk')
    disable_staff(core.path,'front_desk')
    with pytest.raises(AccessDenied): desk.create_patient('No',30,'Female')
    assert not core.records('SELECT * FROM patients')


def test_password_reset_revokes_sessions_and_old_password(core):
    user=staff(core,'nurse')
    reset_password(core.path,'nurse','Replacement-test-password')
    with pytest.raises(AccessDenied): authenticate(core.path,user._token)
    with pytest.raises(AccessDenied): login(core.path,'nurse',PASSWORD)
    assert login(core.path,'nurse','Replacement-test-password')


def test_protected_first_admin_setup(core,monkeypatch):
    from hospital.auth import bootstrap_admin
    code='test-only-setup-code-with-strong-length-12345'
    monkeypatch.setenv('MEDAGENT_SETUP_TOKEN',code)
    with pytest.raises(AccessDenied): bootstrap_admin(core.path,'wrong','owner',PASSWORD)
    assert not core.records('SELECT * FROM staff_users')
    bootstrap_admin(core.path,code,'owner',PASSWORD)
    assert authenticate(core.path,login(core.path,'owner',PASSWORD))['role']=='admin'
    with pytest.raises(AccessDenied): bootstrap_admin(core.path,code,'second-owner',PASSWORD)
