import datetime

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
