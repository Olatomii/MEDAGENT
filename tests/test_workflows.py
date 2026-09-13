import datetime
import os
import tempfile

# Importing Streamlit's app initializes the database. Never use the demo database.
_test_directory = tempfile.TemporaryDirectory()
os.environ['MEDAGENT_DB_PATH'] = os.path.join(_test_directory.name, 'test.db')

import app


def setup_function():
    conn = app.get_db_connection()
    conn.execute("DELETE FROM appointments")
    conn.execute("DELETE FROM waitlist")
    conn.execute("DELETE FROM audit_events")
    conn.commit()
    conn.close()


def add_appointment(name, doc_id=1, triage=4, date=None):
    date = date or datetime.date.today().isoformat()
    conn = app.get_db_connection()
    cur = conn.execute(
        """INSERT INTO appointments
           (booking_date, doc_id, patient_name, age, gender, address, occupation,
            payment_status, triage_level, status, location, queue_number, notes)
           VALUES (?, ?, ?, 30, 'Male', '', '', 'Cleared', ?, 'WAITING',
                   'Nurses Station', ?, '')""",
        (date, doc_id, name, triage, app.HospitalAgents.queue_number()),
    )
    conn.commit()
    rowid = cur.lastrowid
    conn.close()
    return rowid


def test_rollover_uses_doctor_specialty_without_schema_error():
    old_date = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    rowid = add_appointment("Rollover Patient", date=old_date)
    app.HospitalAgents().run_rollover(datetime.date.today().isoformat())
    conn = app.get_db_connection()
    new_date = conn.execute("SELECT booking_date FROM appointments WHERE rowid=?", (rowid,)).fetchone()[0]
    conn.close()
    assert new_date == datetime.date.today().isoformat()


def test_lab_results_do_not_change_triage_level():
    rowid = add_appointment("Lab Patient", triage=5)
    app.HospitalAgents().upload_lab_results(rowid, "Lab Patient")
    conn = app.get_db_connection()
    triage, location = conn.execute("SELECT triage_level, location FROM appointments WHERE rowid=?", (rowid,)).fetchone()
    conn.close()
    assert (triage, location) == (5, "Doctor Wait")


def test_vacuum_skips_blocked_specialty_and_promotes_next_candidate():
    today = datetime.date.today().isoformat()
    for doc_id in (1, 2):
        for index in range(3):
            add_appointment(f"GP {doc_id}-{index}", doc_id=doc_id)
    conn = app.get_db_connection()
    for name, triage, specialty in (("Blocked GP", 3, "General Practice"), ("Eligible Cardio", 4, "Cardiology")):
        conn.execute(
            """INSERT INTO waitlist
               (booking_date, patient_name, age, gender, address, occupation,
                payment_status, triage_level, specialty)
               VALUES (?, ?, 30, 'Female', '', '', 'Cleared', ?, ?)""",
            (today, name, triage, specialty),
        )
    conn.commit()
    conn.close()
    app.HospitalAgents().run_vacuum()
    conn = app.get_db_connection()
    promoted = conn.execute("SELECT COUNT(*) FROM appointments WHERE patient_name='Eligible Cardio'").fetchone()[0]
    blocked = conn.execute("SELECT COUNT(*) FROM waitlist WHERE patient_name='Blocked GP'").fetchone()[0]
    conn.close()
    assert promoted == 1
    assert blocked == 1


def test_critical_vitals_commit_before_vacuum():
    rowid = add_appointment("Critical Patient")
    app.HospitalAgents().process_vitals(rowid, "Critical Patient", 190, 37.0, 80, 98, 16)
    conn = app.get_db_connection()
    triage, status, location = conn.execute("SELECT triage_level, status, location FROM appointments WHERE rowid=?", (rowid,)).fetchone()
    conn.close()
    assert (triage, status, location) == (2, "WAITING", "Doctor Wait")


def register(name, date, spec='General Practice', triage=4, payment='Cleared'):
    return app.HospitalAgents().register_patient(name, 30, 'Male', '', '', payment, spec, triage, date)


def test_backlog_counts_toward_routine_capacity():
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    for doc in (1, 2):
        for i in range(3):
            add_appointment(f'Backlog {doc}-{i}', doc_id=doc, date=yesterday)
    assert register('Today', datetime.date.today().isoformat()) == 'WAITLIST'


def test_emergency_cap_across_dates_and_billing_bypass():
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    for i in range(3):
        assert register(f'ED {i}', yesterday, triage=1, payment='Pending') == 'SUCCESS'
    assert register('Fourth ED', datetime.date.today().isoformat(), triage=2) == 'CODE_BLUE'
    assert register('Routine unpaid', yesterday, payment='Pending') == 'BILLING_ERROR'


def test_future_waitlist_is_not_promoted_early():
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    for i in range(6):
        assert register(f'Future {i}', tomorrow) == 'SUCCESS'
    assert register('Future waiting', tomorrow) == 'WAITLIST'
    with app.get_db_connection() as conn:
        conn.execute("UPDATE appointments SET status='COMPLETED'")
    app.HospitalAgents().run_vacuum(datetime.date.today().isoformat())
    with app.get_db_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM waitlist').fetchone()[0] == 1


def test_repeated_vacuum_promotes_only_once():
    today = datetime.date.today().isoformat()
    for i in range(6):
        register(f'Routine {i}', today)
    register('Waiting patient', today)
    with app.get_db_connection() as conn:
        conn.execute("UPDATE appointments SET status='ABSENT' WHERE rowid=(SELECT MIN(rowid) FROM appointments)")
    agent = app.HospitalAgents()
    agent.run_vacuum(today)
    agent.run_vacuum(today)
    with app.get_db_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments WHERE patient_name='Waiting patient'").fetchone()[0] == 1


def test_critical_ed_overflow_keeps_transfer_record():
    today = datetime.date.today().isoformat()
    for i in range(3):
        register(f'ED {i}', today, triage=2)
    rowid = add_appointment('Deteriorating patient')
    app.HospitalAgents().process_vitals(rowid, 'Deteriorating patient', 190, 37, 80, 98, 16)
    with app.get_db_connection() as conn:
        assert conn.execute('SELECT triage_level,status,location FROM appointments WHERE rowid=?', (rowid,)).fetchone() == (2, 'DIVERTED', 'Transfer Required')
