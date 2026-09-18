"""Development entry point: streamlit run app_v2.py."""
import os
import datetime as dt
from pathlib import Path
import streamlit as st
from hospital.service import Hospital, TRANSITIONS
from hospital.agents import process_events

st.set_page_config(page_title='MedAgent | Patient workspace',page_icon='✚',layout='wide')
st.markdown('<style>'+Path(__file__).with_name('style.css').read_text()+'</style>',unsafe_allow_html=True)
hospital=Hospital(os.getenv('MEDAGENT_V2_DB_PATH','medagent_v2.db'))
process_events(hospital.path)
st.sidebar.title('MedAgent Sync')
st.sidebar.caption('Patient & agent workspace')
page=st.sidebar.radio('Workspace',['Patients','Appointments','Care workspace','Doctor sessions','Agent decisions'])
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
        if st.form_submit_button('Create patient',type='primary'):
            act(lambda:hospital.create_patient(name,age,gender,email),'Patient created. ID:')
    search=st.text_input('Find a patient by name or ID')
    filtered=[p for p in patients if search.lower() in (p['name']+' '+p['patient_id']).lower()]
    if filtered:
        st.dataframe(filtered,hide_index=True,use_container_width=True)
        selected=st.selectbox('Patient history',[p['patient_id'] for p in filtered],format_func=patient_names.get)
        history=hospital.records('''SELECT a.appointment_id,a.service_date,a.specialty,a.status,v.visit_id,v.state,v.notes
            FROM appointments a LEFT JOIN visits v USING(appointment_id) WHERE a.patient_id=? ORDER BY a.service_date DESC''',(selected,))
        st.dataframe(history,hide_index=True,use_container_width=True) if history else st.info('No appointments recorded for this patient.')
    else:
        st.info('No matching patient records.')

elif page=='Doctor sessions':
    st.caption('Set the number of bookings a doctor can accept on a date. Consultation duration is not fixed. New dates start with no configured capacity.')
    names={d['doctor_id']:d['name']+' · '+d['specialty'] for d in doctors}
    with st.form('session'):
        doctor=st.selectbox('Doctor',list(names),format_func=names.get)
        date=st.date_input('Session date',min_value=dt.date.fromisoformat(hospital.today()))
        capacity=st.number_input('Booking capacity',min_value=0,max_value=100,value=3)
        if st.form_submit_button('Save session',type='primary'):
            act(lambda:hospital.set_session(doctor,date.isoformat(),capacity),'Session saved; eligible waitlist entries checked.')
    st.dataframe(hospital.records('''SELECT s.service_date,d.name,d.specialty,s.capacity FROM sessions s JOIN doctors d USING(doctor_id) ORDER BY s.service_date,d.name'''),hide_index=True,use_container_width=True)

elif page=='Appointments':
    st.caption('Reserve an appointment first. Check-in on the appointment date starts a separate visit.')
    if not patients:
        st.info('Create a patient in Patients, then configure a doctor session before booking.')
    else:
        patient=st.selectbox('Patient',list(patient_names),format_func=patient_names.get)
        date=st.date_input('Appointment date',min_value=dt.date.fromisoformat(hospital.today()))
        specialty=st.selectbox('Department',sorted({d['specialty'] for d in doctors}))
        urgency=st.selectbox('Staff-assigned urgency',[1,2] if specialty=='Emergency / Trauma' else [3,4,5])
        st.caption('Email sending is not connected to this development database yet. The existing preview reminder service is separate.')
        if st.button('Book appointment',type='primary'):
            act(lambda:hospital.book(patient,date.isoformat(),specialty,urgency),'Booking recorded. Check its confirmation status below. ID:')
    bookings=hospital.records('''SELECT a.*,p.name,d.name AS doctor FROM appointments a JOIN patients p USING(patient_id)
        LEFT JOIN doctors d ON d.doctor_id=a.doctor_id ORDER BY service_date DESC,a.rowid DESC''')
    st.subheader('Bookings')
    if not bookings:
        st.info('No appointments yet.')
    for a in bookings:
        with st.expander(f"{a['name']} · {a['service_date']} · {a['status']}"):
            st.caption(f"{a['appointment_id']} · {a['specialty']} · {a['doctor'] or 'Awaiting allocation'}")
            if a['status']=='CONFIRMED' and a['service_date']==hospital.today():
                if st.button('Check in',key='in'+a['appointment_id']):
                    act(lambda:hospital.check_in(a['appointment_id']),'Patient checked in. Visit:')
            if a['status'] in ('CONFIRMED','WAITLISTED'):
                if st.button('Cancel appointment',key='cancel'+a['appointment_id']):
                    act(lambda:hospital.close_booking(a['appointment_id'],'CANCELLED'),'Appointment cancelled; waitlist checked.')
            if a['status']=='CONFIRMED' and a['service_date']<hospital.today():
                if st.button('Mark missed',key='miss'+a['appointment_id']):
                    act(lambda:hospital.close_booking(a['appointment_id'],'MISSED'),'Appointment marked missed.')

elif page=='Care workspace':
    st.caption('Only checked-in patients appear here. Clinical actions are chosen by staff; the system validates each transition.')
    visits=hospital.records('''SELECT v.*,a.urgency,a.specialty,p.name FROM visits v JOIN appointments a USING(appointment_id)
        JOIN patients p USING(patient_id) WHERE v.state NOT IN ('COMPLETED','TRANSFER_REQUIRED')
        ORDER BY a.urgency,v.checked_in_at,v.rowid''')
    if not visits:
        st.info('No active visits. Check in a confirmed appointment to begin.')
    for v in visits:
        with st.expander(f"{v['name']} · {v['state']} · urgency {v['urgency']}",expanded=True):
            st.caption(v['visit_id']+' · '+v['specialty'])
            st.text(v['notes'] or 'No clinical notes recorded.')
            widget_id=v['visit_id']+str(v['version'])
            target=st.selectbox('Next care step',sorted(TRANSITIONS[v['state']]),key='target'+widget_id)
            notes=st.text_area('Staff notes / directive',key='notes'+widget_id)
            if st.button('Record care step',key='move'+widget_id,type='primary'):
                act(lambda:hospital.transition(v['visit_id'],v['version'],target,notes),'Care step recorded.')

else:
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
