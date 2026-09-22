"""Public project overview; no patient records or privileged actions."""
import streamlit as st


def introduction():
    st.header('MedAgent Sync')
    st.subheader('Agent-based hospital appointment and patient management')
    st.write('A working prototype connecting appointment booking, patient arrival, '
             'care coordination and hospital capacity through traceable workflow events.')
    left, middle, right = st.columns(3)
    with left:
        st.markdown('**Appointments**')
        st.write('Book by department, manage daily capacity and promote eligible waitlisted patients when a place opens.')
    with middle:
        st.markdown('**Patient journey**')
        st.write('Follow a visit through check-in, assessment, consultation, diagnostics, pharmacy and ward care.')
    with right:
        st.markdown('**Accountable decisions**')
        st.write('Inspect the event, affected record and reason behind each recorded agent decision.')
    with st.expander('Explore the workflow without an account'):
        st.markdown('''1. A staff member configures a doctor’s session capacity.
2. A patient requests an appointment; capacity determines confirmation or waitlisting.
3. A cancellation releases a place. The waitlist agent checks urgency and waiting order.
4. Staff check in the patient and record care progress. Consultation length is not fixed.
5. Workflow events and decision reasons make the process reviewable.

Agents are rule-based handlers in a shared Python process. The waitlist agent
allocates released places; other handlers record appointment and care decisions.
Staff remain responsible for clinical actions. This walkthrough does not create records.''')
    st.caption('Portfolio prototype · Use fictional patients only. Clinical rules are simulation logic, '
               'not validated medical guidance. Hosted records may reset after a restart or deployment; '
               'email delivery is disabled on the public demo.')
    st.divider()
