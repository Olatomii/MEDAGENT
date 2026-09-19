import datetime as dt
import tempfile
from pathlib import Path
import streamlit as st
from . import accounts
from .auth import ROLES,change_own_password
from .portal import PatientPortal
from .backup import snapshot
from .evaluation import compare


def action(callback,message):
    try:
        callback()
    except ValueError as exc:
        st.error(str(exc))
    else:
        st.session_state.feedback=message
        st.rerun()


def password_form(path,token):
    with st.form('password-change',clear_on_submit=True):
        old=st.text_input('Current password',type='password')
        new=st.text_input('New password (12–256 characters)',type='password')
        confirm=st.text_input('Repeat new password',type='password')
        if st.form_submit_button('Change password and sign out'):
            if new!=confirm:
                st.error('Passwords do not match.')
            else:
                try:
                    change_own_password(path,token,old,new)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.clear()
                    st.rerun()


def staff_management(path,token):
    users=accounts.list_accounts(path,token)
    st.caption('Create individual staff accounts. Share temporary passwords privately; users must change them before accessing workspaces.')
    with st.form('new-staff',clear_on_submit=True):
        username=st.text_input('New staff username')
        role=st.selectbox('Staff role',ROLES)
        password=st.text_input('Temporary password (12–256 characters)',type='password')
        if st.form_submit_button('Create staff account',type='primary'):
            action(lambda:accounts.add_staff(path,token,username,password,role),'Staff account created.')
    st.dataframe(users,hide_index=True)
    selected=st.selectbox('Manage account',[u['user_id'] for u in users],format_func=lambda uid:next(u['username'] for u in users if u['user_id']==uid))
    target=next(u for u in users if u['user_id']==selected)
    roles=('patient',) if target['role']=='patient' else ROLES
    role=st.selectbox('Assigned role',roles,index=roles.index(target['role']),key='role'+str(selected))
    enabled=st.checkbox('Account enabled',value=bool(target['enabled']),key='enabled'+str(selected))
    st.caption('Changing access revokes that account’s current sessions.')
    if st.button('Save account access'):
        action(lambda:accounts.manage_account(path,token,selected,role,enabled),'Account access updated.')
    with st.form('reset-account',clear_on_submit=True):
        password=st.text_input('New temporary password',type='password')
        if st.form_submit_button('Reset selected account password'):
            action(lambda:accounts.reset_account(path,token,selected,password),'Password reset; previous sessions revoked.')


def invite_panel(path,token,patient_id):
    st.caption('Verify the patient’s identity before sharing an invitation. Each code is single-use and expires after 48 hours.')
    if st.button('Create patient portal invitation',key='invite'+patient_id):
        try:
            code=accounts.invite_patient(path,token,patient_id)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.code(code,language=None)
            st.info('Copy this code now and give it privately to this patient. It is not sent by email automatically.')


def patient_portal(path,token,today):
    portal=PatientPortal(path,token)
    profile=portal.profile()
    st.title('My appointments')
    st.caption(profile['name']+' · '+profile['patient_id'])
    st.info('For urgent concerns contact the hospital directly. This portal requests routine appointments; it does not assess symptoms.')
    if st.session_state.get('feedback'):
        st.success(st.session_state.pop('feedback'))
    with st.expander('Request a routine appointment'):
        date=st.date_input('Requested date',min_value=dt.date.fromisoformat(today))
        specialty=st.selectbox('Department',['General Practice','Cardiology','Orthopedics'])
        consent=st.checkbox('I agree to an email reminder',disabled=date.isoformat()<=today)
        if st.button('Request appointment'):
            action(lambda:portal.book(date.isoformat(),specialty,consent if date.isoformat()>today else False),'Request saved. Check confirmation or waitlist status below. Billing is handled by the front desk.')
    appointments=portal.appointments()
    if not appointments:
        st.info('You have no appointments yet.')
    for a in appointments:
        with st.expander(f"{a['service_date']} · {a['specialty']} · {a['status']}",expanded=True):
            st.caption('Doctor: '+str(a['doctor'] or 'Awaiting allocation')+' · Billing: '+a['billing_status'])
            key=a['appointment_id']+str(a['revision'])
            if a['status']=='CONFIRMED' and a['service_date']>=today:
                st.caption('Attendance: '+('confirmed' if a['attendance_confirmed'] else 'not confirmed'))
                if st.button('Confirm I plan to attend',key='attend'+key):
                    action(lambda:portal.confirm_attendance(a['appointment_id']),'Attendance confirmed. Check in with staff when you arrive.')
            if a['status'] in ('CONFIRMED','WAITLISTED'):
                if st.button('Cancel my appointment',key='cancel'+key):
                    action(lambda:portal.cancel(a['appointment_id']),'Appointment cancelled.')
                date=st.date_input('Change date',value=max(dt.date.fromisoformat(today),dt.date.fromisoformat(a['service_date'])),min_value=dt.date.fromisoformat(today),key='date'+key)
                waiting=st.checkbox('I accept losing my old place and joining the waitlist if the new date is full',key='waiting'+key)
                if st.button('Change my appointment',key='change'+key):
                    action(lambda:portal.reschedule(a['appointment_id'],a['revision'],date.isoformat(),waiting),'Date changed. Review the updated status.')
                consent=st.checkbox('Email reminder enabled',value=bool(a['reminder_opt_in']),key='consent'+key)
                if st.button('Save reminder preference',key='saveconsent'+key):
                    action(lambda:portal.consent(a['appointment_id'],consent),'Reminder preference saved.')
    with st.expander('Verify my email'):
        st.caption('Recorded email: '+(profile['email'] or 'None. Ask the front desk to update it.'))
        if profile['email'] and profile['email']==profile['verified_email']:
            st.success('Email address verified.')
        else:
            if st.button('Send verification email'):
                action(portal.request_verification,'Verification requested. Check your email.')
            code=st.text_input('Verification code')
            if st.button('Verify code'):
                action(lambda:portal.verify_email(code),'Email verified.')


def evaluation_backup(path,token):
    accounts.require_user(path,token,{'admin'})
    st.subheader('Background services')
    from .database import connection
    with connection(path) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_status'").fetchone()
        statuses=[dict(r) for r in conn.execute('SELECT * FROM maintenance_status ORDER BY job')] if exists else []
    if statuses:
        st.dataframe(statuses,hide_index=True)
        heartbeat=next((r for r in statuses if r['job']=='worker'),None)
        if not heartbeat or (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(heartbeat['attempted_at'])).total_seconds()>180:
            st.warning('Worker heartbeat is overdue. Check the host before relying on scheduled reminders or backups.')
        if any(r['state'] in ('ERROR','REVIEW_REQUIRED') for r in statuses):
            st.warning('A background job needs attention. Review the status above.')
    else:
        st.info('The background worker has not reported a maintenance cycle yet.')
    st.caption('Automatic snapshots on the same disk help recover earlier records but do not protect against loss of that disk. Keep a private off-site copy as well.')
    st.subheader('Synthetic queue evaluation')
    st.caption('Same synthetic arrivals and variable durations for both policies. This separate experiment is not a clinical validation or a claim about real hospital performance. Routine aging is experimental and not used by the live queue.')
    seed=st.number_input('Random seed',min_value=0,max_value=100000,value=42)
    count=st.number_input('Simulated patients',min_value=20,max_value=1000,value=120)
    doctors=st.number_input('Simulated doctors',min_value=1,max_value=20,value=2)
    results=compare(seed,count,doctors)
    st.dataframe(results,hide_index=True)
    st.caption('Both policies eventually serve all simulated patients. Waiting time is measured in simulated minutes; consultations are non-preemptive.')
    st.subheader('Database backup')
    st.caption('The download includes patient records, account password hashes and audit history. Store it privately. Sessions and unused invitation/verification codes are excluded. Restore uses a new destination and requires trusted host access.')
    if st.button('Prepare backup download'):
        with tempfile.TemporaryDirectory() as directory:
            backup=snapshot(path,Path(directory)/'medagent-backup.db')
            st.download_button('Download private backup',data=backup.read_bytes(),file_name='medagent-backup.db',mime='application/octet-stream')
