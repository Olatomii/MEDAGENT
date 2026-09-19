"""Development entry point: streamlit run app_v2.py."""
import os
import datetime as dt
from pathlib import Path
import streamlit as st
from hospital.service import Hospital, TRANSITIONS
from hospital.agents import process_events
from hospital import notifications
from reminders import valid_email
from hospital.auth import initialize_auth,login,logout,AccessDenied,bootstrap_admin,authenticate
from hospital.access import StaffHospital,PAGES
from hospital.accounts import accept_invitation
from hospital.ui_accounts import password_form,staff_management,invite_panel,patient_portal,evaluation_backup

st.set_page_config(page_title='MedAgent | Patient workspace',page_icon='✚',layout='wide')
st.markdown('<style>'+Path(__file__).with_name('style.css').read_text()+'</style>',unsafe_allow_html=True)
core=Hospital(os.getenv('MEDAGENT_V2_DB_PATH','medagent_v2.db'))
initialize_auth(core.path)
if not st.session_state.get('staff_token'):
    if not core.records('SELECT user_id FROM staff_users LIMIT 1'):
        st.title('Set up your administrator account')
        st.info('Retrieve MEDAGENT_SETUP_TOKEN from this service’s Environment settings in Render. It is a private setup code, not your new password.')
        with st.form('setup',clear_on_submit=True):
            setup_token=st.text_input('Private setup code',type='password')
            username=st.text_input('Choose administrator username')
            password=st.text_input('Choose password (at least 12 characters)',type='password')
            confirmation=st.text_input('Confirm password',type='password')
            if st.form_submit_button('Create administrator',type='primary'):
                try:
                    if password!=confirmation:
                        raise ValueError('Passwords do not match.')
                    bootstrap_admin(core.path,setup_token,username,password)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.clear()
                    st.rerun()
        st.caption('Setup is locked after the first account is created. There are no default credentials.')
        st.stop()
    st.title('MedAgent sign-in')
    with st.form('signin',clear_on_submit=True):
        username=st.text_input('Username')
        password=st.text_input('Password',type='password')
        if st.form_submit_button('Sign in',type='primary'):
            try:
                token=login(core.path,username,password)
            except AccessDenied as exc:
                st.error(str(exc))
            else:
                st.session_state.clear()
                st.session_state.staff_token=token
                st.rerun()
    with st.expander('Activate patient invitation'):
        with st.form('activate-patient',clear_on_submit=True):
            invitation=st.text_input('Private invitation code',type='password')
            new_username=st.text_input('Choose patient username')
            new_password=st.text_input('Choose patient password (12–256 characters)',type='password')
            if st.form_submit_button('Activate patient account'):
                try:
                    accept_invitation(core.path,invitation,new_username,new_password)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success('Patient account created. Sign in above.')
    st.caption('Staff accounts are created by administrators. Patient accounts require a private invitation from the hospital.')
    st.stop()
hospital=StaffHospital(core,st.session_state.staff_token)
try:
    staff=authenticate(core.path,st.session_state.staff_token)
except AccessDenied:
    st.session_state.clear()
    st.rerun()
if staff['must_change']:
    st.title('Choose your own password')
    st.info('Change your temporary password before accessing any patient or staff workspace.')
    password_form(core.path,st.session_state.staff_token)
    if st.button('Sign out instead'):
        logout(core.path,st.session_state.staff_token)
        st.session_state.clear()
        st.rerun()
    st.stop()
process_events(hospital.path)
st.sidebar.title('MedAgent Sync')
st.sidebar.caption('Patient & agent workspace')
st.sidebar.caption(staff['username']+' · '+staff['role'].replace('_',' '))
if st.sidebar.button('Sign out'):
    logout(hospital.path,st.session_state.staff_token)
    st.session_state.clear()
    st.rerun()
with st.sidebar.expander('Change my password'):
    password_form(core.path,st.session_state.staff_token)
if staff['role']=='patient':
    patient_portal(core.path,st.session_state.staff_token,core.today())
    st.stop()
page=st.sidebar.radio('Workspace',PAGES[staff['role']])
hospital.authorize_page(page)
st.sidebar.info('Development version · separate patient database')
st.title(page)
if st.session_state.get('feedback'):
    st.success(st.session_state.pop('feedback'))


def act(callback, message):
    try:
        result=callback()
    except (ValueError, TypeError) as exc:
        st.error(str(exc))
        return
    st.session_state.feedback=message+(f' {result}' if isinstance(result,str) else '')
    st.rerun()


patients=hospital.records('SELECT * FROM patients ORDER BY name,patient_id')
patient_names={p['patient_id']:f"{p['name']} · {p['patient_id']}" for p in patients}
doctors=hospital.records('SELECT * FROM doctors ORDER BY specialty,doctor_id')
if page=='Patients':
    st.caption('Create one permanent record per patient. Use their ID for each subsequent appointment.')
    with st.form('patient'):
        name=st.text_input('Full name')
        left,right=st.columns(2)
        age=left.number_input('Age',min_value=0,max_value=120,value=30)
        gender=right.selectbox('Gender',['Male','Female','Other / not specified'])
        email=st.text_input('Email address (optional)')
        suggestion=notifications.suggested_email(email)
        if suggestion:
            st.warning('Check the spelling: did you mean '+suggestion+'? The address is not changed automatically.')
        if st.form_submit_button('Create patient',type='primary'):
            act(lambda:hospital.create_patient(name,age,gender,email),'Patient created. ID:')
    search=st.text_input('Find a patient by name or ID')
    filtered=[p for p in patients if search.lower() in (p['name']+' '+p['patient_id']).lower()]
    if filtered:
        st.dataframe(filtered,hide_index=True,use_container_width=True)
        selected=st.selectbox('Patient history',[p['patient_id'] for p in filtered],format_func=patient_names.get)
        with st.expander('Patient portal access'):
            invite_panel(core.path,st.session_state.staff_token,selected)
        contact=next(p for p in filtered if p['patient_id']==selected)
        edited_email=st.text_input('Update patient email',value=contact['email'],key='email'+selected+contact['email'])
        suggestion=notifications.suggested_email(edited_email)
        if suggestion:
            st.warning('Did you mean '+suggestion+'?')
        st.caption('Changing the address clears reminder consent on unstarted appointments. Confirm consent again under Appointments.')
        if st.button('Save email'):
            act(lambda:hospital.update_email(selected,contact['email'],edited_email),'Email saved.')
        history=hospital.records('''SELECT appointment_id,service_date,specialty,status FROM appointments
            WHERE patient_id=? ORDER BY service_date DESC''',(selected,))
        if staff['role']=='admin':
            history=hospital.records('''SELECT a.appointment_id,a.service_date,a.specialty,a.status,v.visit_id,v.state,v.notes
                FROM appointments a LEFT JOIN visits v USING(appointment_id) WHERE a.patient_id=? ORDER BY a.service_date DESC''',(selected,))
        st.dataframe(history,hide_index=True,use_container_width=True) if history else st.info('No appointments recorded for this patient.')
        readings=hospital.records('''SELECT t.* FROM vitals t JOIN visits v USING(visit_id)
            JOIN appointments a USING(appointment_id) WHERE a.patient_id=? ORDER BY t.vital_id DESC''',(selected,)) if staff['role']=='admin' else []
        if readings:
            st.subheader('Recorded vitals')
            st.dataframe(readings,hide_index=True)
    else:
        st.info('No matching patient records.')

elif page=='Doctor sessions':
    st.caption('Routine capacity is the number of bookings for a date. Emergency capacity is simultaneous places, including unresolved earlier visits. Consultation duration is not fixed; configure each staffed date.')
    names={d['doctor_id']:d['name']+' · '+d['specialty'] for d in doctors}
    with st.form('session'):
        doctor=st.selectbox('Doctor',list(names),format_func=names.get)
        date=st.date_input('Session date',min_value=dt.date.fromisoformat(hospital.today()))
        capacity=st.number_input('Booking capacity',min_value=0,max_value=100,value=3)
        if st.form_submit_button('Save session',type='primary'):
            act(lambda:hospital.set_session(doctor,date.isoformat(),capacity),'Session saved; eligible waitlist entries checked.')
    st.dataframe(hospital.records('''SELECT s.service_date,d.name,d.specialty,s.capacity FROM sessions s JOIN doctors d USING(doctor_id) ORDER BY s.service_date,d.name'''),hide_index=True,use_container_width=True)
    st.subheader('Working hours, leave and breaks')
    sessions=hospital.records('SELECT * FROM sessions ORDER BY service_date DESC,doctor_id')
    if sessions:
        index=st.selectbox('Session to manage',range(len(sessions)),format_func=lambda i:sessions[i]['service_date']+' · '+names[sessions[i]['doctor_id']])
        session=sessions[index]
        key=str(session['doctor_id'])+session['service_date']
        start=st.time_input('Session begins',dt.time.fromisoformat(session['start_time']),key='start'+key)
        end=st.time_input('Session ends',dt.time.fromisoformat(session['end_time']),key='end'+key)
        enabled=st.checkbox('Doctor available (untick for leave)',value=bool(session['enabled']),key='available'+key)
        st.caption('Leave stops new bookings and consultation starts. Existing bookings remain listed for staff to contact and reschedule.')
        if st.button('Save availability'):
            act(lambda:hospital.set_availability(session['doctor_id'],session['service_date'],start.strftime('%H:%M'),end.strftime('%H:%M'),enabled),'Availability saved.')
        affected=hospital.records("SELECT appointment_id,patient_id,status FROM appointments WHERE doctor_id=? AND service_date=? AND status IN ('CONFIRMED','CHECKED_IN')",(session['doctor_id'],session['service_date']))
        if not session['enabled'] and affected:
            st.warning('Review these bookings because the doctor is unavailable.')
            st.dataframe(affected,hide_index=True)
        with st.form('break'+key):
            bstart=st.time_input('Break begins',dt.time(12))
            bend=st.time_input('Break ends',dt.time(13))
            reason=st.text_input('Reason')
            if st.form_submit_button('Add break'):
                act(lambda:hospital.add_break(session['doctor_id'],session['service_date'],bstart.strftime('%H:%M'),bend.strftime('%H:%M'),reason),'Break recorded.')
        for interval in hospital.records('SELECT * FROM doctor_breaks WHERE doctor_id=? AND service_date=?',(session['doctor_id'],session['service_date'])):
            st.caption(interval['start_time']+'–'+interval['end_time']+' · '+interval['reason'])
            if st.button('Remove break',key='removebreak'+str(interval['break_id'])):
                act(lambda:hospital.remove_break(interval['break_id']),'Break removed.')

elif page=='Appointments':
    st.caption('Reserve an appointment first. Check-in on the appointment date starts a separate visit.')
    if not patients:
        st.info('Create a patient in Patients, then configure a doctor session before booking.')
    else:
        patient=st.selectbox('Patient',list(patient_names),format_func=patient_names.get)
        date=st.date_input('Appointment date',min_value=dt.date.fromisoformat(hospital.today()))
        specialty=st.selectbox('Department',sorted({d['specialty'] for d in doctors}))
        urgency=st.selectbox('Staff-assigned urgency',[1,2] if specialty=='Emergency / Trauma' else [3,4,5])
        reminder_opt_in=False
        if specialty!='Emergency / Trauma' and date.isoformat()>hospital.today():
            reminder_opt_in=st.checkbox('Patient agrees to an email reminder')
            selected_patient=next(p for p in patients if p['patient_id']==patient)
            if reminder_opt_in:
                st.caption('Reminder recipient: '+(selected_patient['email'] or 'No email recorded'))
                if not valid_email(selected_patient['email']):
                    st.error('A valid patient email is required before opting in.')
                suggestion=notifications.suggested_email(selected_patient['email'])
                if suggestion:
                    st.warning('Did you mean '+suggestion+'? Correct the patient email before booking if necessary.')
            st.caption('One reminder the day before a confirmed, billing-cleared appointment, after 9 am in the configured reminder timezone.')
        if not notifications.configured():
            st.info('Email delivery for this development version is not activated. Consent can still be recorded.')
        if st.button('Book appointment',type='primary'):
            act(lambda:hospital.book(patient,date.isoformat(),specialty,urgency,reminder_opt_in),'Booking recorded. Check its confirmation status below. ID:')
    bookings=hospital.records('''SELECT a.*,p.name,d.name AS doctor FROM appointments a JOIN patients p USING(patient_id)
        LEFT JOIN doctors d ON d.doctor_id=a.doctor_id ORDER BY service_date DESC,a.rowid DESC''')
    st.subheader('Bookings')
    if not bookings:
        st.info('No appointments yet.')
    for a in bookings:
        with st.expander(f"{a['name']} · {a['service_date']} · {a['status']}"):
            st.caption(f"{a['appointment_id']} · {a['specialty']} · {a['doctor'] or 'Awaiting allocation'}")
            st.caption('Billing: '+a['billing_status']+' · Email reminder: '+('opted in' if a['reminder_opt_in'] else 'off'))
            st.caption('Planned attendance: '+('patient confirmed' if a['attendance_confirmed'] else 'not confirmed'))
            if a['status'] in ('CONFIRMED','WAITLISTED'):
                if a['billing_status']=='PENDING':
                    reference=st.text_input('Billing reference / staff record',key='billing'+a['appointment_id'])
                    if st.button('Record billing clearance',key='clear'+a['appointment_id']):
                        act(lambda:hospital.clear_billing(a['appointment_id'],reference),'Billing clearance recorded. No payment was charged by this application.')
                if a['reminder_opt_in']:
                    if st.button('Withdraw reminder consent',key='optout'+a['appointment_id']):
                        act(lambda:hospital.reminder_preference(a['appointment_id'],False),'Reminder consent withdrawn.')
                elif a['urgency']>2 and a['service_date']>hospital.today():
                    consent=st.checkbox('Patient agrees to reminders for this appointment',key='consent'+a['appointment_id'])
                    if st.button('Enable reminder',key='optin'+a['appointment_id'],disabled=not consent):
                        act(lambda:hospital.reminder_preference(a['appointment_id'],True),'Reminder consent recorded.')
            if a['status']=='CONFIRMED' and a['service_date']==hospital.today():
                if st.button('Check in',key='in'+a['appointment_id']):
                    act(lambda:hospital.check_in(a['appointment_id']),'Patient checked in. Visit:')
            if a['status'] in ('CONFIRMED','WAITLISTED'):
                if st.button('Cancel appointment',key='cancel'+a['appointment_id']):
                    act(lambda:hospital.close_booking(a['appointment_id'],'CANCELLED'),'Appointment cancelled; waitlist checked.')
                if a['urgency']>2:
                    change_key=a['appointment_id']+str(a['revision'])
                    new_date=st.date_input('New appointment date',value=max(dt.date.fromisoformat(a['service_date']),dt.date.fromisoformat(hospital.today())),
                                           min_value=dt.date.fromisoformat(hospital.today()),key='newdate'+change_key)
                    accept_waitlist=st.checkbox('If the new date is full, release my old booking and join the new date’s waitlist',key='acceptwait'+change_key)
                    st.caption('The department and billing clearance stay with this appointment. Earlier emails cannot be recalled; inform the patient of the date change.')
                    if st.button('Reschedule appointment',key='reschedule'+change_key):
                        act(lambda:hospital.reschedule(a['appointment_id'],a['revision'],new_date.isoformat(),accept_waitlist),'Rescheduled. New status:')
            if a['status']=='CONFIRMED' and a['service_date']<hospital.today():
                if st.button('Mark missed',key='miss'+a['appointment_id']):
                    act(lambda:hospital.close_booking(a['appointment_id'],'MISSED'),'Appointment marked missed.')

elif page=='Care workspace':
    st.caption('Only checked-in patients appear here. Clinical actions are chosen by staff; the system validates each transition.')
    visits=hospital.records('''SELECT v.*,d.specialty,p.name,d.name AS doctor FROM visits v JOIN appointments a USING(appointment_id)
        JOIN patients p USING(patient_id) LEFT JOIN doctors d ON d.doctor_id=v.assigned_doctor_id
        WHERE v.state NOT IN ('COMPLETED','TRANSFER_REQUIRED','ADMITTED')
        ORDER BY v.urgency,v.checked_in_at,v.rowid''')
    visible={'nurse':{'ASSESSMENT'},'physician':{'CONSULTATION','DIAGNOSTICS'},'pharmacy':{'PHARMACY'}}
    if staff['role']!='admin':
        visits=[v for v in visits if v['state'] in visible.get(staff['role'],set())]
    if not visits:
        st.info('No active visits. Check in a confirmed appointment to begin.')
    for v in visits:
        with st.expander(f"{v['name']} · {v['state']} · urgency {v['urgency']}",expanded=True):
            st.caption(v['visit_id']+' · '+str(v['specialty'])+' · '+str(v['doctor']))
            st.text(v['notes'] or 'No clinical notes recorded.')
            readings=hospital.records('SELECT systolic,temperature,heart_rate,spo2,respiratory_rate,flagged,recorded_at FROM vitals WHERE visit_id=? ORDER BY vital_id DESC',(v['visit_id'],))
            if readings:
                st.dataframe(readings,hide_index=True)
            widget_id=v['visit_id']+str(v['version'])
            if v['state']=='CONSULTATION':
                active=hospital.records('SELECT started_at FROM consultations WHERE visit_id=? AND finished_at IS NULL',(v['visit_id'],))
                if active:
                    st.info('Consultation in progress. Selecting the next care step ends this consultation segment.')
                elif hospital.can('start_consultation'):
                    if st.button('Start consultation',key='startconsult'+widget_id):
                        act(lambda:hospital.start_consultation(v['visit_id'],v['version']),'Consultation started.')
                samples=hospital.records('SELECT (finished_at-started_at)/60 AS minutes FROM consultations WHERE doctor_id=? AND finished_at IS NOT NULL ORDER BY consultation_id DESC LIMIT 30',(v['assigned_doctor_id'],))
                if len(samples)>=3:
                    import statistics
                    typical=statistics.median(r['minutes'] for r in samples)
                    ahead=hospital.records("SELECT COUNT(*) AS n FROM visits WHERE assigned_doctor_id=? AND state='CONSULTATION' AND visit_id!=? AND (urgency<? OR (urgency=? AND checked_in_at<=?))",(v['assigned_doctor_id'],v['visit_id'],v['urgency'],v['urgency'],v['checked_in_at']))[0]['n']
                    st.caption(f'Observed median consultation: {typical:.0f} min; approximate queue wait: {ahead*typical:.0f} min. Estimate changes with urgency and actual consultation lengths.')
                else:
                    st.caption('Not enough completed consultation timings for a waiting-time estimate.')
            if v['state']=='ASSESSMENT':
                st.caption('Prototype vital-sign routing rules are for simulation; staff must assess clinical urgency.')
                with st.form('vitals'+widget_id):
                    c1,c2=st.columns(2)
                    bp=c1.number_input('Systolic BP (mmHg)',min_value=0,max_value=300,value=120)
                    temperature=c2.number_input('Temperature (°C)',min_value=20.0,max_value=45.0,value=37.0)
                    pulse=c1.number_input('Heart rate (bpm)',min_value=0,max_value=250,value=75)
                    oxygen=c2.number_input('SpO2 (%)',min_value=0,max_value=100,value=98)
                    respiration=c1.number_input('Respiratory rate',min_value=0,max_value=60,value=16)
                    if st.form_submit_button('Record vitals and route',type='primary'):
                        act(lambda:hospital.record_vitals(v['visit_id'],v['version'],bp,temperature,pulse,oxygen,respiration),'Vitals recorded. Review the updated care state or transfer queue.')
                continue
            target=st.selectbox('Next care step',sorted(TRANSITIONS[v['state']]),key='target'+widget_id)
            notes=st.text_area('Diagnostic results' if v['state']=='DIAGNOSTICS' else 'Staff notes / prescription / directive',key='notes'+widget_id)
            ward=None
            if target=='ADMITTED':
                wards=hospital.records('SELECT * FROM wards ORDER BY ward_id')
                names={w['ward_id']:w['name'] for w in wards}
                ward=st.selectbox('Ward selected by staff',list(names),format_func=names.get,key='ward'+widget_id)
            if st.button('Record care step',key='move'+widget_id,type='primary'):
                if target=='ADMITTED':
                    act(lambda:hospital.admit(v['visit_id'],v['version'],ward,notes),'Ward admission recorded.')
                else:
                    act(lambda:hospital.transition(v['visit_id'],v['version'],target,notes),'Care step recorded.')
    transfers=hospital.records('''SELECT p.name,v.visit_id,v.notes FROM visits v JOIN appointments a USING(appointment_id)
        JOIN patients p USING(patient_id) WHERE v.state='TRANSFER_REQUIRED' ''')
    if transfers:
        st.warning('Transfer-required records: these indicate a need for staff action, not a completed external transfer.')
        st.dataframe(transfers,hide_index=True)

elif page=='Wards':
    wards=hospital.records('''SELECT w.*,COUNT(v.visit_id) AS occupied FROM wards w LEFT JOIN visits v
        ON v.ward_id=w.ward_id AND v.state='ADMITTED' GROUP BY w.ward_id ORDER BY w.ward_id''')
    st.caption('Configure capacity before admission. A place is released when staff record discharge or transfer.')
    for w in wards:
        st.subheader(w['name'])
        st.caption(f"Occupied: {w['occupied']} / {w['capacity']}")
        if hospital.can('set_ward_capacity'):
            capacity=st.number_input('Ward capacity',min_value=0,value=w['capacity'],key='cap'+str(w['ward_id']))
            if st.button('Save capacity',key='savecap'+str(w['ward_id'])):
                act(lambda:hospital.set_ward_capacity(w['ward_id'],capacity),'Ward capacity saved.')
        for v in hospital.records('''SELECT v.*,p.name FROM visits v JOIN appointments a USING(appointment_id)
            JOIN patients p USING(patient_id) WHERE v.state='ADMITTED' AND v.ward_id=?''',(w['ward_id'],)):
            with st.expander(v['name']+' · '+v['visit_id']):
                key=v['visit_id']+str(v['version'])
                notes=st.text_area('Discharge / transfer notes',key='disnotes'+key)
                outcome=st.selectbox('Outcome',['COMPLETED','TRANSFER_REQUIRED'],key='outcome'+key)
                if st.button('End ward stay',key='discharge'+key):
                    act(lambda:hospital.transition(v['visit_id'],v['version'],outcome,notes),'Ward stay ended; bed released.')
    unresolved=hospital.records("SELECT visit_id,version FROM visits WHERE state='ADMITTED' AND ward_id IS NULL")
    if unresolved:
        st.warning('Some older v2 admissions have no ward assignment. These need reconciliation before relying on occupancy totals.')
        st.dataframe(unresolved,hide_index=True)
        for v in unresolved:
            ward=st.selectbox('Reconcile ward for '+v['visit_id'],[w['ward_id'] for w in wards],
                              format_func=lambda wid:next(w['name'] for w in wards if w['ward_id']==wid),key='reconcile'+v['visit_id'])
            if st.button('Assign existing admission',key='assign'+v['visit_id']+str(v['version'])):
                act(lambda:hospital.admit(v['visit_id'],v['version'],ward),'Existing admission assigned to ward.')

elif page=='Agent decisions':
    st.caption('Events and decisions are persisted. Pending events resume on the next page interaction or when the dispatcher is run. No autonomous background worker is claimed in this version.')
    pending=hospital.records('SELECT event_id,kind,entity_id,attempts,error FROM events WHERE processed_at IS NULL')
    if pending:
        st.warning('Some events are pending. Failed events require investigation; they have not been discarded.')
        st.dataframe(pending,hide_index=True)
        if st.button('Retry pending events'):
            act(lambda:process_events(hospital.path),'Event processing attempted.')
    else:
        st.success('All recorded events have been processed.')
    st.subheader('Decision history')
    st.dataframe(hospital.records('SELECT created_at,agent,entity_id,reason,event_id FROM decisions ORDER BY decision_id DESC LIMIT 200'),hide_index=True,use_container_width=True)
    st.subheader('Email delivery')
    st.caption('Sending is '+('configured' if notifications.configured() else 'disabled')+'. Run the v2 worker for unattended checks. ACCEPTED means provider acceptance, not confirmed inbox delivery. SENDING or REVIEW_REQUIRED needs provider verification before retrying.')
    st.dataframe(hospital.records('SELECT appointment_id,appointment_date,status,error_code,updated_at FROM notifications ORDER BY updated_at DESC LIMIT 100'),hide_index=True)
    st.subheader('Staff audit')
    st.dataframe(hospital.records('SELECT user_id,action,outcome,created_at FROM staff_audit ORDER BY audit_id DESC LIMIT 100'),hide_index=True)
    st.caption('Event actor IDs identify the initiating staff account; null identifies older events or trusted maintenance. Background decisions retain their triggering event link.')
    st.dataframe(hospital.records('SELECT event_id,kind,entity_id,actor_id,created_at FROM events ORDER BY event_id DESC LIMIT 100'),hide_index=True)

elif page=='Staff management':
    staff_management(core.path,st.session_state.staff_token)

elif page=='Evaluation & backup':
    evaluation_backup(core.path,st.session_state.staff_token)
