"""Registration and operational interfaces using native accessible controls."""
import streamlit as st
from . import experience
from .ui_accounts import action


def registration(path):
    with st.expander('New patient? Create an account'):
        st.caption('Your patient ID is generated automatically. If you already have a hospital record, ask staff for an invitation to that record.')
        with st.form('self-registration',clear_on_submit=True):
            name=st.text_input('Your full name',max_chars=120)
            age=st.number_input('Your age',min_value=0,max_value=120,value=18)
            gender=st.selectbox('Your gender',['Male','Female','Other / not specified'])
            email=st.text_input('Your email address')
            username=st.text_input('Your new username',max_chars=80)
            password=st.text_input('Your new password (12–256 characters)',type='password',max_chars=256)
            confirm=st.text_input('Confirm your new password',type='password',max_chars=256)
            consent=st.checkbox('I am creating my own patient account and understand staff may need to check my identity.')
            if st.form_submit_button('Create my patient account',type='primary'):
                try:
                    if not consent or password!=confirm:
                        raise ValueError('Confirm your password and accept the account statement.')
                    pid=experience.register(path,name,age,gender,email,username,password)
                except ValueError as exc: st.error(str(exc))
                else: st.success('Account created. Your patient ID is '+pid+'. Sign in above. Email ownership is not yet verified.')


def duplicate_panel(path,token):
    rows=experience.duplicates(path,token)
    with st.expander(f'Identity reviews ({len(rows)})'):
        st.caption('Matching names/ages or emails are possible duplicates, not proof of identity. Accounts and records are never merged automatically.')
        if not rows: st.info('No pending identity reviews.')
        for row in rows:
            st.write(row['name']+' · '+row['patient_id'])
            st.caption('Possible existing record: '+row['candidate_name']+' · '+row['candidate_id'])
            key=row['patient_id']+row['candidate_id']
            note=st.text_input('Identity check and outcome',key='dupnote'+key)
            confirmed=st.checkbox('I verified these are different people. Keep both records.',key='distinct'+key)
            st.caption('If this is the same person, do not clear this review: an administrator should disable the new account and issue access to the original record after identity checks. No history is deleted.')
            if st.button('Keep as separate patients',key='dupresolve'+key,disabled=not confirmed):
                action(lambda:experience.resolve_duplicate(path,token,row['patient_id'],row['candidate_id'],note),'Identity review recorded.')


def reassignment(path,token,a):
    choices=experience.reassignment_options(path,token,a['appointment_id'])
    with st.expander('Change assigned doctor'):
        if not choices:
            st.info('No other doctor in this department has a free place on this date. Configure availability or reschedule.')
            return
        labels={r['doctor_id']:f"{r['name']} · {r['capacity']-r['booked']} places available" for r in choices}
        key=a['appointment_id']+str(a['revision'])
        doctor=st.selectbox('Available doctor',list(labels),format_func=labels.get,key='assign'+key)
        reason=st.text_input('Reason for changing doctor',key='assignreason'+key)
        st.caption('The date and billing clearance stay the same. The portal records the change and asks the patient to reconfirm attendance; no immediate email is sent.')
        if st.button('Confirm doctor change',key='reassign'+key):
            action(lambda:experience.reassign(path,token,a['appointment_id'],a['revision'],doctor,reason),'Doctor reassigned.')


def dashboard(path,token):
    data=experience.operational_snapshot(path,token)
    st.caption('Today · '+data['date']+' · updates when you refresh this page')
    if st.button('Refresh dashboard'): st.rerun()
    counts={r['status']:r['count'] for r in data['bookings']}
    columns=st.columns(3)
    columns[0].metric('Booked today',sum(counts.values()))
    columns[1].metric('Checked in / completed',counts.get('CHECKED_IN',0)+counts.get('FULFILLED',0))
    columns[2].metric('Waiting for a place',counts.get('WAITLISTED',0))
    st.caption('Counts are appointment statuses for today; cancelled/missed records remain included in the total. Checked in / completed counts arrivals, not attendance promises.')
    if data['bookings']: st.dataframe(data['bookings'],hide_index=True,use_container_width=True)
    st.subheader('Doctor workload')
    if data['workload']: st.dataframe(data['workload'],hide_index=True,use_container_width=True)
    else: st.info('No doctor sessions configured for today.')
    if data['waits']:
        import pandas as pd
        st.subheader('Time in current care state')
        frame=pd.DataFrame(data['waits'])
        st.dataframe(frame.groupby('State')['Minutes in state'].agg(['count','median','max']).reset_index(),hide_index=True,use_container_width=True)
        st.caption('Elapsed time in current state, including active work; not a prediction or a clinical urgency score.')
    st.subheader('Agent follow-up queue')
    st.caption('Rules flag unavailable doctors, unresolved transfers, and waits over 60 minutes (120 for diagnostics). These are operational review thresholds, not clinical deadlines. No automatic change to triage or care. Refresh to re-evaluate.')
    if not data['alerts']: st.success('No current follow-up flags.')
    for alert in data['alerts']:
        with st.expander(alert['patient']+' · '+alert['reason'],expanded=not bool(alert['review'])):
            st.caption(alert['record'])
            if alert['minutes'] is not None: st.write('Elapsed in state: '+str(alert['minutes'])+' minutes')
            if alert['review']:
                st.info('Last follow-up: '+alert['review']['note']+' · '+alert['review']['reviewed_at']+' UTC. The flag stays visible while its condition remains.')
            note=st.text_input('Follow-up action taken',key='followup'+alert['key'])
            if st.button('Record follow-up',key='review'+alert['key']):
                action(lambda:experience.review_alert(path,token,alert['key'],note),'Follow-up recorded.')
