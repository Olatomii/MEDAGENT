"""Small presentation helpers shared by the public and authenticated screens."""
from html import escape
import streamlit as st


def status_badge(status):
    """Always include a readable label; colour is only a secondary cue."""
    value = str(status)
    tone = ('success' if value in {'CONFIRMED', 'FULFILLED', 'COMPLETED', 'DISCHARGED'}
            else 'waiting' if value in {'WAITLISTED', 'WAITING', 'PENDING'}
            else 'attention' if value in {'TRANSFER_REQUIRED', 'CANCELLED', 'MISSED'}
            else 'neutral')
    st.markdown(f'<span class="status-badge {tone}">{escape(value.replace("_", " ").title())}</span>',
                unsafe_allow_html=True)


def empty_state(title, detail):
    st.markdown(f'<div class="empty-state"><strong>{escape(title)}</strong>'
                f'<p>{escape(detail)}</p></div>', unsafe_allow_html=True)


def sidebar_identity(username, role):
    st.sidebar.markdown('<div class="workspace-brand"><strong>MEDAGENT</strong>'
                        '<span>Hospital workspace</span></div>', unsafe_allow_html=True)
    st.sidebar.markdown(f'<div class="account-summary"><strong>{escape(username)}</strong>'
                        f'<span>{escape(role.replace("_", " ").title())}</span></div>',
                        unsafe_allow_html=True)


def page_description(page):
    descriptions = {
        'Overview': 'Your day at a glance. Appointments, capacity and follow-ups in one place.',
        'Appointments': 'Coordinate bookings, arrivals and the waiting list.',
        'Care workspace': 'Keep each visit moving, with the next care action in view.',
        'Wards': 'Manage ward capacity and ongoing patient care.',
        'Agent decisions': 'See what the agents decided and the reasons behind each action.',
        'Staff management': 'Manage the people and roles in your hospital workspace.',
        'Evaluation & backup': 'Review simulated performance and protect your saved records.',
    }
    if page in descriptions:
        st.caption(descriptions[page])
