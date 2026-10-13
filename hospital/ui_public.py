"""Public entry page and a fictional, read-only patient journey."""
from pathlib import Path
import base64
import streamlit as st
from .ui_theme import status_badge


@st.cache_data(show_spinner=False)
def welcome_image():
    return base64.b64encode((Path(__file__).resolve().parent.parent / 'assets' / 'care-welcome.webp').read_bytes()).decode('ascii')


def choose_route(label):
    st.session_state.public_route = label


def introduction():
    route = st.session_state.get('public_route')
    with st.container(key='public-brand'):
        st.header('MEDAGENT')
        st.caption('Hospital Appointment & Patient Management')
    if route is None:
        with st.container(key='welcome'):
            left, right = st.columns([1.05, 1], gap='large')
            with left:
                st.markdown('<div class="welcome-copy"><h1>Care, better connected.</h1>'
                            '<p>Your appointments, care and patient records in one place.</p></div>', unsafe_allow_html=True)
                st.button('Patient Portal', key='entry_Patient Portal', type='primary',
                          on_click=choose_route, args=('Patient Portal',), use_container_width=True)
                st.caption('Book a visit, review appointments and stay up to date.')
            with right:
                st.markdown('<img class="welcome-image" alt="Illustration of a clinician and patient talking in a welcoming clinic" '
                            'src="data:image/webp;base64,'+welcome_image()+'">', unsafe_allow_html=True)
        with st.container(key='entry-options'):
            staff, demo = st.columns([1, 1], gap='large')
            with staff:
                st.subheader('For the care team')
                st.write('Manage appointments and coordinate every patient visit.')
                st.button('Staff Portal', key='entry_Staff Portal', on_click=choose_route, args=('Staff Portal',))
            with demo:
                st.subheader('Take a look around')
                st.write('Follow a sample patient journey. No account needed.')
                st.button('Explore Demo', key='entry_Explore Demo', on_click=choose_route, args=('Explore Demo',))
    else:
        with st.container(key='portal-navigation'):
            for column, label in zip(st.columns(3), ('Patient Portal', 'Staff Portal', 'Explore Demo')):
                column.button(label, key='entry_'+label, on_click=choose_route, args=(label,),
                              type='primary' if route == label else 'secondary', use_container_width=True)
    st.caption('Portfolio demonstration using sample data. Not intended for clinical use.')
    with st.expander('About this demo'):
        st.write('MedAgent demonstrates appointment booking, waiting lists, patient check-in, '
                 'care coordination and staff access. Use fictional patient details only.')
        st.write('Explore Demo shows a fictional patient journey without an account. '
                 'Its sample records are illustrative and do not change any patient records.')
        st.write('Clinical workflows and urgency rules are simulated and have not been clinically '
                 'validated. This application must not be used for diagnosis, treatment or real patient care.')
        st.caption('Email delivery is disabled on the hosted demo.')
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
        stages = ('Appointment', 'Arrival', 'Consultation', 'Follow-up')
        st.markdown('<ol class="journey">'+''.join(
            '<li'+(' aria-current="step"' if item == stage else '')+'>'+item+'</li>'
            for item in stages)+'</ol>', unsafe_allow_html=True)
        with st.container(border=True):
            status_badge(status)
            st.subheader(status)
            st.write(detail)
            st.write(explanation)
        with st.expander('What happens when a place becomes available?'):
            st.write('When an eligible booking is cancelled, the waiting list is checked by urgency '
                     'and waiting order. A patient can be offered the available place, and the reason is recorded.')
    return route
