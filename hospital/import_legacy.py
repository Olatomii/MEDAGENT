"""Explicit, atomic import of a read-only legacy snapshot into a separate database."""
import json
import sqlite3
from pathlib import Path
from contextlib import closing
from .database import connection, identifier
from .agents import emit


def import_snapshot(hospital, source):
    source=Path(source).resolve()
    if source==Path(hospital.path).resolve():
        raise ValueError('Source and destination must be different databases.')
    # Store an exact source record so unsupported legacy fields are not discarded.
    with connection(hospital.path,write=True) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS legacy_records (appointment_id TEXT PRIMARY KEY REFERENCES appointments,record_json TEXT NOT NULL)')
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as legacy:
            legacy.row_factory=sqlite3.Row
            doctors={r['doc_id']:dict(r) for r in legacy.execute('SELECT * FROM doctors')}
            count=0
            for table in ('appointments','waitlist'):
                for row in legacy.execute(f'SELECT rowid AS source_row,* FROM {table}'):
                    data=dict(row)
                    original=json.dumps(data,sort_keys=True)
                    previous=conn.execute('''SELECT r.record_json FROM legacy_imports i JOIN legacy_records r USING(appointment_id)
                        WHERE source=? AND source_table=? AND source_row=?''',
                        (str(source),table,data['source_row'])).fetchone()
                    if previous:
                        if previous[0]!=original:
                            raise ValueError('Source changed after import. Use an immutable snapshot; do not overwrite imported records.')
                        continue
                    patient_id,appointment_id=identifier('P'),identifier('A')
                    conn.execute('INSERT INTO patients(patient_id,name,age,gender,email) VALUES (?,?,?,?,?)',
                        (patient_id,data['patient_name'],data['age'],data['gender'],data.get('email','')))
                    doctor_id=None
                    specialty=data.get('specialty')
                    if table=='appointments':
                        old_doc=doctors.get(data['doc_id'])
                        if old_doc is None:
                            raise ValueError('Legacy appointment has an unknown doctor; fix the snapshot before importing.')
                        specialty=old_doc['specialty']
                        doctor=conn.execute('SELECT doctor_id FROM doctors WHERE name=? AND specialty=?',(old_doc['name'],specialty)).fetchone()
                        doctor_id=doctor[0] if doctor else conn.execute('INSERT INTO doctors(name,specialty) VALUES (?,?)',(old_doc['name'],specialty)).lastrowid
                    state=None
                    status='WAITLISTED'
                    if table=='appointments':
                        old_status=data['status']
                        if old_status=='ABSENT':
                            status='MISSED'
                        elif old_status in ('COMPLETED','DIVERTED'):
                            status='FULFILLED'
                            state='COMPLETED' if old_status=='COMPLETED' else 'TRANSFER_REQUIRED'
                        elif old_status=='ADMITTED':
                            status,state='CHECKED_IN','ADMITTED'
                        elif old_status=='WAITING':
                            locations={'Nurses Station':'ASSESSMENT','Doctor Wait':'CONSULTATION','Imaging/Lab':'DIAGNOSTICS','Pharmacy':'PHARMACY'}
                            if data['location'] not in locations:
                                raise ValueError('Unknown legacy location: '+str(data['location']))
                            # Legacy registration did not distinguish arrival; leave unassessed records unstarted.
                            state=None if data['location']=='Nurses Station' else locations[data['location']]
                            status='CONFIRMED' if state is None else 'CHECKED_IN'
                        else:
                            raise ValueError('Unknown legacy status: '+str(old_status))
                    conn.execute('INSERT INTO appointments(appointment_id,patient_id,service_date,specialty,urgency,doctor_id,status,reminder_opt_in) VALUES (?,?,?,?,?,?,?,?)',
                        (appointment_id,patient_id,data['booking_date'],specialty,data['triage_level'],doctor_id,status,data.get('reminder_opt_in',0)))
                    if state:
                        conn.execute('INSERT INTO visits(visit_id,appointment_id,state,notes) VALUES (?,?,?,?)',
                            (identifier('V'),appointment_id,state,data.get('notes') or ''))
                    conn.execute('INSERT INTO legacy_imports VALUES (?,?,?,?)',(str(source),table,data['source_row'],appointment_id))
                    conn.execute('INSERT INTO legacy_records VALUES (?,?)',(appointment_id,original))
                    emit(conn,'LEGACY_IMPORTED',appointment_id,reason='Imported original record without merging patient identities; source snapshot retained.')
                    count+=1
            return count
