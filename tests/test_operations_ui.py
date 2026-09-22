from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
from hospital.service import Hospital
from hospital.auth import initialize_auth,create_staff,login
from hospital.accounts import add_staff,invite_patient,accept_invitation
from hospital.access import PAGES

PASSWORD='Synthetic-test-password-42'
APP=str(Path(__file__).resolve().parents[1]/'app_v2.py')


def test_public_overview_on_fresh_database(tmp_path,monkeypatch):
    path=str(tmp_path/'fresh.db')
    monkeypatch.setenv('MEDAGENT_V2_DB_PATH',path)
    app=AppTest.from_file(APP,default_timeout=20).run()
    assert not app.exception
    assert any(h.value=='MedAgent Sync' for h in app.header)
    assert any(e.label=='Explore the workflow without an account' for e in app.expander)
    assert any(e.label=='Administrator setup' for e in app.expander)
    assert not app.sidebar.radio
    h=Hospital(path)
    assert not h.records('SELECT * FROM patients')
    assert not h.records('SELECT * FROM staff_users')

@pytest.fixture
def ui(tmp_path,monkeypatch):
    path=str(tmp_path/'ui.db')
    monkeypatch.setenv('MEDAGENT_V2_DB_PATH',path)
    h=Hospital(path)
    initialize_auth(path)
    create_staff(path,'owner',PASSWORD,'admin')
    h.set_session(1,h.today(),3)
    p=h.create_patient('Synthetic patient',30,'Female','person@example.com')
    a=h.book(p,h.today(),'General Practice')
    h.clear_billing(a,'TEST')
    v=h.check_in(a)
    h.record_vitals(v,0,120,37,75,98,16)
    return h,p


def test_all_staff_pages_with_populated_records(ui):
    h,_=ui
    for role,pages in PAGES.items():
        username='owner' if role=='admin' else role
        if role!='admin': create_staff(h.path,username,PASSWORD,role)
        app=AppTest.from_file(APP,default_timeout=20)
        app.session_state['staff_token']=login(h.path,username,PASSWORD)
        app.run()
        assert not app.exception
        for page in pages:
            app.sidebar.radio[0].set_value(page).run()
            assert not app.exception, (role,page,[e.message for e in app.exception])


def test_patient_activation_and_scoped_screen(ui):
    h,p=ui
    admin=login(h.path,'owner',PASSWORD)
    code=invite_patient(h.path,admin,p)
    app=AppTest.from_file(APP,default_timeout=20).run()
    by_label=lambda label:next(w for w in app.text_input if w.label==label)
    by_label('Private invitation code').set_value(code)
    by_label('Choose patient username').set_value('patient')
    by_label('Choose patient password (12–256 characters)').set_value(PASSWORD)
    next(b for b in app.button if b.label=='Activate patient account').click().run()
    assert not app.exception and app.success
    by_label('Username').set_value('patient')
    by_label('Password').set_value(PASSWORD)
    next(b for b in app.button if b.label=='Sign in').click().run()
    assert not app.exception
    assert app.title[0].value=='My appointments'
    assert not app.sidebar.radio


def test_temporary_password_ui_gate(ui):
    h,_=ui
    add_staff(h.path,login(h.path,'owner',PASSWORD),'nurse',PASSWORD,'nurse')
    app=AppTest.from_file(APP,default_timeout=20)
    app.session_state['staff_token']=login(h.path,'nurse',PASSWORD)
    app.run()
    assert app.title[0].value=='Choose your own password' and not app.sidebar.radio
    values={'Current password':PASSWORD,'New password (12–256 characters)':'Changed-password-42','Repeat new password':'Changed-password-42'}
    for widget in app.text_input: widget.set_value(values[widget.label])
    next(b for b in app.button if b.label=='Change password and sign out').click().run()
    assert not app.exception
    assert app.title[0].value=='MedAgent sign-in'
    assert login(h.path,'nurse','Changed-password-42')


def test_self_registration_signin_and_booking(ui):
    h,_=ui
    app=AppTest.from_file(APP,default_timeout=20).run()
    values={'Your full name':'New portal patient','Your email address':'new@example.com','Your new username':'newportal',
            'Your new password (12–256 characters)':PASSWORD,'Confirm your new password':PASSWORD}
    for w in app.text_input:
        if w.label in values:w.set_value(values[w.label])
    next(w for w in app.checkbox if w.label.startswith('I am creating')).check()
    next(b for b in app.button if b.label=='Create my patient account').click().run()
    assert not app.exception and app.success
    next(w for w in app.text_input if w.label=='Username').set_value('newportal')
    next(w for w in app.text_input if w.label=='Password').set_value(PASSWORD)
    next(b for b in app.button if b.label=='Sign in').click().run()
    next(b for b in app.button if b.label=='Request appointment').click().run()
    assert not app.exception and not app.error
    assert any('Appointment updates'==e.label for e in app.expander)
