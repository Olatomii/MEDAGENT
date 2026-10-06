"""Public entry page and a fictional, read-only patient journey."""
import streamlit as st


def introduction():
    st.header('MEDAGENT')
    st.subheader('Hospital Appointment & Patient Management')
    st.write('Your appointments, care and patient records in one place.')
    for column, label in zip(st.columns(3), ('Patient Portal', 'Staff Portal', 'Explore Demo')):
        if column.button(label, key='entry_'+label, use_container_width=True):
            st.session_state.public_route = label
    st.caption('Portfolio demonstration using sample data. Not intended for clinical use.')
    with st.expander('About this demo'):
        st.write('MedAgent demonstrates appointment booking, waiting lists, patient check-in, '
                 'care coordination and staff access. Use fictional patient details only.')
        st.write('Explore Demo shows a fictional patient journey without an account. '
                 'Its sample records are illustrative and do not change any patient records.')
        st.write('Clinical workflows and urgency rules are simulated and have not been clinically '
                 'validated. This application must not be used for diagnosis, treatment or real patient care.')
        st.caption('Saved demo accounts and records may reset. Email delivery is disabled on the hosted demo.')
    route = st.session_state.get('public_route')
    if route == 'Explore Demo':
        st.divider()
        st.subheader('A patient’s journey')
        st.caption('Fictional sample · Read-only walkthrough')
        stage = st.radio('Explore a stage', ('Appointment', 'Arrival', 'Consultation', 'Follow-up'), horizontal=True)
        examples = {
            'Appointment': ('Appointment confirmed', 'Alex Morgan · General Practice',
                            'A place is reserved in the doctor’s session. When a session is full, a new request joins the waiting list.'),
            'Arrival': ('Checked in', 'Alex Morgan · Waiting for assessment',
                        'Staff confirm arrival and prepare the visit. Appointment and visit history stay linked to the patient.'),
            'Consultation': ('Consultation in progress', 'Alex Morgan · With the care team',
                             'Staff record progress and coordinate any diagnostics or pharmacy steps. Consultation length is not fixed.'),
            'Follow-up': ('Visit completed', 'Alex Morgan · Visit history updated',
                          'The completed visit remains in the patient’s history. A future appointment can be requested when needed.'),
        }
        status, detail, explanation = examples[stage]
        with st.container(border=True):
            st.markdown('**'+status+'**')
            st.write(detail)
            st.write(explanation)
        with st.expander('What happens when a place becomes available?'):
            st.write('When an eligible booking is cancelled, the waiting list is checked by urgency '
                     'and waiting order. A patient can be offered the available place, and the reason is recorded.')
    return route
