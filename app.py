import sqlite3
import pandas as pd
import streamlit as st
import random
import datetime

# --- 1. DATABASE SETUP & THREAD SAFETY ---
DB_NAME = 'medagent_enterprise.db'

def get_db_connection():
    # check_same_thread=False prevents crashes when deployed to cloud servers
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS doctors
                 (doc_id INTEGER PRIMARY KEY, name TEXT, specialty TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS appointments
                 (booking_date TEXT, doc_id INTEGER, patient_name TEXT, age INTEGER, gender TEXT, 
                  address TEXT, occupation TEXT, payment_status TEXT, triage_level INTEGER, 
                  status TEXT, location TEXT, queue_number TEXT, notes TEXT, 
                  added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute("SELECT COUNT(*) FROM doctors")
    if c.fetchone()[0] < 21:
        c.execute("DELETE FROM doctors")
        docs = [
            (1, "Dr. Smith", "General"), (2, "Dr. Taylor", "General"), (3, "Dr. Williams", "General"),
            (4, "Dr. Jones", "Cardiology"), (5, "Dr. Davis", "Cardiology"), (6, "Dr. Miller", "Cardiology"),
            (7, "Dr. Brown", "Neurology"), (8, "Dr. Wilson", "Neurology"), (9, "Dr. Moore", "Neurology"),
            (10, "Dr. Adams", "Gynecology"), (11, "Dr. King", "Gynecology"), (12, "Dr. Wright", "Gynecology"),
            (13, "Dr. Clark", "General Surgery"), (14, "Dr. Hill", "General Surgery"), (15, "Dr. Scott", "General Surgery"),
            (16, "Dr. White", "Nephrology"), (17, "Dr. Green", "Nephrology"), (18, "Dr. Baker", "Nephrology"),
            (19, "Dr. Evans", "Emergency / Trauma"), (20, "Dr. Carter", "Emergency / Trauma"), (21, "Dr. Mitchell", "Emergency / Trauma")
        ]
        c.executemany("INSERT INTO doctors (doc_id, name, specialty) VALUES (?, ?, ?)", docs)
    conn.commit()
    conn.close()

init_db()


# --- 2. MULTI-AGENT MODELS ---
class HospitalAgents:
    def __init__(self):
        if 'logs' not in st.session_state:
            st.session_state.logs = []
        if 'dynamic_alert' not in st.session_state:
            st.session_state.dynamic_alert = None
    
    def log(self, message, is_critical=False):
        st.session_state.logs.append(message)
        if is_critical:
            st.session_state.dynamic_alert = message

    def register_patient(self, name, age, gender, address, occ, payment, spec, triage, date):
        if payment != "Cleared" and triage > 2:
            self.log(f"[BILLING_AGENT] REJECT: {name} must clear billing before vitals/consultation.", True)
            return False

        if triage in [1, 2]:
            spec = "Emergency / Trauma"
            self.log(f"[TRIAGE_AGENT] [ALERT] Level {triage} Emergency. {name} autonomously routed to Emergency Unit.", True)

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT doc_id, name FROM doctors WHERE specialty=?", (spec,))
        docs = c.fetchall()
        
        if not docs:
            conn.close()
            return False

        doc_loads = []
        for doc_id, doc_name in docs:
            c.execute("SELECT COUNT(*) FROM appointments WHERE doc_id=? AND booking_date<=? AND status NOT IN ('COMPLETED', 'ABSENT', 'ADMITTED')", (doc_id, date))
            count = c.fetchone()[0]
            doc_loads.append((count, doc_id, doc_name))
            
        doc_loads.sort(key=lambda x: x[0])
        best_doc_id = doc_loads[0][1]
        best_doc_name = doc_loads[0][2]

        q_num = f"Q-{random.randint(1000, 9999)}"
        loc = "Doctor Wait" if triage == 1 else "Nurses Station"
        
        c.execute("INSERT INTO appointments (booking_date, doc_id, patient_name, age, gender, address, occupation, payment_status, triage_level, status, location, queue_number, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'WAITING', ?, ?, '')",
                  (date, best_doc_id, name, age, gender, address, occ, payment, triage, loc, q_num))
        conn.commit()
        self.log(f"[ROUTING_AGENT] {name} registered ({q_num}). Assigned to {best_doc_name} (Load: {doc_loads[0][0]} pts). Routed to {loc}.")
        conn.close()
        return True

    def process_vitals(self, rowid, name, bp_sys, temp, hr, spo2, rr):
        conn = get_db_connection()
        c = conn.cursor()
        if bp_sys > 180 or temp > 39.0 or hr > 120 or spo2 < 92 or rr > 24:
            new_note = f"[VITALS]: BP {bp_sys}, HR {hr}, SpO2 {spo2}% [CRITICAL]"
            c.execute("UPDATE appointments SET triage_level=2, location='Doctor Wait', notes = notes || ? WHERE rowid=?", (new_note, rowid))
            self.log(f"[CLINICAL_PREP] [CRITICAL] Vitals for {name} indicate distress. Autonomously upgraded to Level 2.", True)
        else:
            new_note = f"[VITALS]: BP {bp_sys}, HR {hr}, SpO2 {spo2}%"
            c.execute("UPDATE appointments SET location='Doctor Wait', notes = notes || ? WHERE rowid=?", (new_note, rowid))
            self.log(f"[CLINICAL_PREP] Vitals logged for {name}. Patient routed to Doctor's Queue.")
        conn.commit()
        conn.close()

    def admit_to_ward(self, rowid, name, age, gender):
        if age < 18:
            ward = "Children's Ward"
        elif gender == "Male":
            ward = "Male Ward"
        else:
            ward = "Female Ward"
            
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE appointments SET location=?, status='ADMITTED' WHERE rowid=?", (ward, rowid))
        conn.commit()
        conn.close()
        self.log(f"[BED_ALLOCATION] {name} processed. Bed locked in {ward}.", True)
        
    def discharge_from_ward(self, rowid, name, ward):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE appointments SET location='Discharged', status='COMPLETED' WHERE rowid=?", (rowid,))
        conn.commit()
        conn.close()
        self.log(f"[BED_ALLOCATION] {name} has been medically cleared and discharged. Bed released in {ward}.")

    def send_to_lab(self, rowid, name, directive):
        conn = get_db_connection()
        c = conn.cursor()
        new_note = f" | [LAB RQ]: {directive}"
        c.execute("UPDATE appointments SET location='Imaging/Lab', notes = notes || ? WHERE rowid=?", (new_note, rowid))
        conn.commit()
        conn.close()
        self.log(f"[DIAGNOSTIC_AGENT] {name} removed from Doctor Queue and routed to Imaging/Lab for {directive}.")

    def upload_lab_results(self, rowid, name):
        conn = get_db_connection()
        c = conn.cursor()
        new_note = " | [LAB]: RESULTS READY"
        c.execute("UPDATE appointments SET location='Doctor Wait', notes = notes || ?, triage_level=2 WHERE rowid=?", (new_note, rowid))
        conn.commit()
        conn.close()
        self.log(f"[DIAGNOSTIC_AGENT] Results uploaded for {name}. Patient injected to top of Doctor Queue.", True)

    def send_to_pharmacy(self, rowid, name, directive):
        conn = get_db_connection()
        c = conn.cursor()
        new_note = f" | [PHARM RQ]: {directive}"
        c.execute("UPDATE appointments SET location='Pharmacy', notes = notes || ? WHERE rowid=?", (new_note, rowid))
        conn.commit()
        conn.close()
        self.log(f"[PHARMACY_AGENT] {name} discharged and routed to Pharmacy. Rx: {directive}.")

    def flag_absent(self, rowid, name, location):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE appointments SET status='ABSENT', location='Archived' WHERE rowid=?", (rowid,))
        conn.commit()
        conn.close()
        self.log(f"[EXCEPTION_AGENT] {name} flagged as ABSENT at {location}. Patient removed from active queue to prevent bottleneck.")


# --- 3. STREAMLIT GUI (PURE DASHBOARD) ---
st.set_page_config(page_title="MedAgent Sync", layout="wide")

def load_css():
    try:
        with open("style.css") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass

load_css()
agent_sys = HospitalAgents()

if st.session_state.get('dynamic_alert'):
    st.markdown(f"<div class='dynamic-island'>{st.session_state.dynamic_alert}</div>", unsafe_allow_html=True)
    st.session_state.dynamic_alert = None


# --- SIDEBAR: AGENT NODE SELECTOR ---
st.sidebar.markdown("""
    <h2 style='font-weight: 800; color: #007AFF; margin-bottom: 0;'>MedAgent Sync</h2>
    <p style='color: #8E8E93; font-size: 12px; font-weight: 700; letter-spacing: 1px; margin-top: 0;'>MAS COORDINATION FRAMEWORK</p>
""", unsafe_allow_html=True)

st.sidebar.markdown("### System Dashboard")

user_role = st.sidebar.radio("View Agent Node:", [
    "Front Desk (Intake)", 
    "Nurses Station (Clinical Prep)", 
    "Physician (Consultation)", 
    "Inpatient Wards", 
    "Diagnostics (Imaging/Lab)", 
    "Pharmacy (Dispensing)", 
    "System Telemetry"
])

st.sidebar.divider()
target_date = st.sidebar.date_input("System Target Date", datetime.date.today()).strftime("%Y-%m-%d")

st.title(f"{user_role}")


# --- ROLE 1: FRONT DESK ---
if user_role == "Front Desk (Intake)":
    with st.form("registration_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        p_name = col1.text_input("Full Name")
        p_gender = col1.selectbox("Gender", ["Male", "Female"])
        p_age = col1.number_input("Age", min_value=0, max_value=120, value=30)
        p_occ = col2.text_input("Occupation")
        p_address = col2.text_input("Living Address")
        
        st.divider()
        col3, col4 = st.columns(2)
        with col3:
            appointment_date = st.date_input("Schedule Appointment Date", datetime.date.today()).strftime("%Y-%m-%d")
        with col4:
            spec = st.selectbox("Routing Department", ["General", "Cardiology", "Neurology", "Gynecology", "General Surgery", "Nephrology", "Emergency / Trauma"])
            
        triage = st.selectbox("Acuity", ["1: Resuscitation", "2: Emergent", "3: Urgent", "4: Semi-Urgent", "5: Routine"], index=4)
        payment = st.radio("Billing Status", ["Pending", "Cleared"], horizontal=True)
        
        submitted = st.form_submit_button("Route to Agent Network", type="primary")
        
        if submitted:
            if not p_name:
                st.error("Patient name is required.")
            else:
                success = agent_sys.register_patient(p_name, p_age, p_gender, p_address, p_occ, payment, spec, int(triage[0]), appointment_date)
                if success:
                    st.toast(f"Patient successfully registered for {appointment_date} and routed to system queue.")
                else:
                    st.error("Registration failed. Ensure billing is cleared for non-emergencies.")


# --- ROLE 2: NURSES STATION ---
elif user_role == "Nurses Station (Clinical Prep)":
    conn = get_db_connection()
    query = """SELECT a.rowid, a.patient_name, a.queue_number, a.triage_level, d.name as doc_name, d.specialty 
               FROM appointments a 
               JOIN doctors d ON a.doc_id = d.doc_id 
               WHERE a.location='Nurses Station' AND a.booking_date<=?"""
    vitals_df = pd.read_sql_query(query, conn, params=(target_date,))
    conn.close()
    
    if vitals_df.empty:
        st.success("Queue is empty. No pending vitals.")
    else:
        for _, row in vitals_df.iterrows():
            with st.expander(f"Patient: {row['patient_name']} ({row['queue_number']}) | L{row['triage_level']} | Routing to: {row['doc_name']} ({row['specialty']})", expanded=True):
                col1, col2, col3 = st.columns(3)
                bp = col1.number_input("Systolic BP (mmHg)", min_value=0, max_value=300, value=120, key=f"bp_{row['rowid']}")
                hr = col2.number_input("Heart Rate (bpm)", min_value=0, max_value=250, value=75, key=f"hr_{row['rowid']}")
                spo2 = col3.number_input("SpO2 (%)", min_value=0, max_value=100, value=98, key=f"spo2_{row['rowid']}")
                temp = col1.number_input("Temp (C)", min_value=20.0, max_value=45.0, value=37.0, key=f"temp_{row['rowid']}")
                rr = col2.number_input("Resp. Rate", min_value=0, max_value=60, value=16, key=f"rr_{row['rowid']}")
                
                col_btn1, col_btn2 = st.columns([3, 1])
                with col_btn1:
                    if st.button("Log Vitals & Trigger Prep Agent", type="primary", key=f"btn_{row['rowid']}", use_container_width=True):
                        agent_sys.process_vitals(row['rowid'], row['patient_name'], bp, temp, hr, spo2, rr)
                        st.rerun()
                with col_btn2:
                    if st.button("Mark Absent", key=f"absent_n_{row['rowid']}", use_container_width=True):
                        agent_sys.flag_absent(row['rowid'], row['patient_name'], "Nurses Station")
                        st.rerun()


# --- ROLE 3: PHYSICIAN ---
elif user_role == "Physician (Consultation)":
    assigned_spec = st.selectbox("Select Physician Specialty Queue:", [
        "General", "Cardiology", "Neurology", 
        "Gynecology", "General Surgery", "Nephrology", "Emergency / Trauma"
    ])
    
    conn = get_db_connection()
    query = """SELECT a.rowid, a.patient_name, a.queue_number, a.triage_level, a.age, a.gender, a.notes, 
                      d.name as doc_name, d.specialty 
               FROM appointments a 
               JOIN doctors d ON a.doc_id = d.doc_id 
               WHERE a.location='Doctor Wait' AND a.booking_date<=? AND d.specialty=?
               ORDER BY a.triage_level ASC"""
               
    doc_df = pd.read_sql_query(query, conn, params=(target_date, assigned_spec))
    conn.close()

    if doc_df.empty:
        st.info(f"No patients currently waiting in the {assigned_spec} queue.")
    else:
        for _, row in doc_df.iterrows():
            with st.expander(f"Assigned to {row['doc_name']} | {row['patient_name']} [L{row['triage_level']}]", expanded=True):
                st.caption(f"{row['age']} yr old {row['gender']} | Queue: {row['queue_number']}")
                st.markdown(f"**Medical Record / Notes:** {row['notes']}")
                
                doc_notes = st.text_input("Clinical Directives (Rx / Lab Orders):", placeholder="Enter specific medical directives...", key=f"doc_notes_{row['rowid']}")
                st.write("")
                
                col_a, col_b, col_c, col_d = st.columns(4)
                if col_a.button("Admit to Ward", key=f"admit_{row['rowid']}", use_container_width=True):
                    agent_sys.admit_to_ward(row['rowid'], row['patient_name'], row['age'], row['gender'])
                    st.rerun()
                    
                if col_b.button("Send to Lab", key=f"lab_{row['rowid']}", use_container_width=True):
                    directive = doc_notes if doc_notes else "Standard Diagnostics"
                    agent_sys.send_to_lab(row['rowid'], row['patient_name'], directive)
                    st.rerun()
                    
                if col_c.button("Discharge (Pharm)", type="primary", key=f"pharm_{row['rowid']}", use_container_width=True):
                    directive = doc_notes if doc_notes else "Standard Dispensing"
                    agent_sys.send_to_pharmacy(row['rowid'], row['patient_name'], directive)
                    st.rerun()
                    
                if col_d.button("Mark Absent", key=f"absent_d_{row['rowid']}", use_container_width=True):
                    agent_sys.flag_absent(row['rowid'], row['patient_name'], "Physician Queue")
                    st.rerun()


# --- ROLE 4: INPATIENT WARDS ---
elif user_role == "Inpatient Wards":
    conn = get_db_connection()
    wards_df = pd.read_sql_query("SELECT rowid, patient_name, age, gender, location, queue_number FROM appointments WHERE status='ADMITTED'", conn)
    conn.close()

    if wards_df.empty:
        st.info("No patients currently admitted to the wards.")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("#### [WARD] Male")
            males = wards_df[wards_df['location'] == 'Male Ward']
            for _, row in males.iterrows():
                with st.container(border=True):
                    st.info(f"Bed Assigned: {row['patient_name']} (Age: {row['age']})")
                    if st.button("Discharge Patient", key=f"d_{row['rowid']}", use_container_width=True):
                        agent_sys.discharge_from_ward(row['rowid'], row['patient_name'], 'Male Ward')
                        st.rerun()
        with col2:
            st.markdown("#### [WARD] Female")
            females = wards_df[wards_df['location'] == 'Female Ward']
            for _, row in females.iterrows():
                with st.container(border=True):
                    st.info(f"Bed Assigned: {row['patient_name']} (Age: {row['age']})")
                    if st.button("Discharge Patient", key=f"d_{row['rowid']}", use_container_width=True):
                        agent_sys.discharge_from_ward(row['rowid'], row['patient_name'], 'Female Ward')
                        st.rerun()
        with col3:
            st.markdown("#### [WARD] Pediatrics")
            kids = wards_df[wards_df['location'] == "Children's Ward"]
            for _, row in kids.iterrows():
                with st.container(border=True):
                    st.info(f"Bed Assigned: {row['patient_name']} (Age: {row['age']})")
                    if st.button("Discharge Patient", key=f"d_{row['rowid']}", use_container_width=True):
                        agent_sys.discharge_from_ward(row['rowid'], row['patient_name'], "Children's Ward")
                        st.rerun()


# --- ROLE 5: DIAGNOSTICS ---
elif user_role == "Diagnostics (Imaging/Lab)":
    conn = get_db_connection()
    lab_df = pd.read_sql_query("SELECT rowid, patient_name, queue_number, notes FROM appointments WHERE location='Imaging/Lab' AND booking_date<=?", conn, params=(target_date,))
    conn.close()
    
    if lab_df.empty:
        st.info("No pending imaging or lab requests.")
    else:
        for _, row in lab_df.iterrows():
            with st.expander(f"Patient: {row['patient_name']} ({row['queue_number']})", expanded=True):
                st.markdown(f"**Medical Record & Directive:** {row['notes']}")
                if st.button("Upload Results & Return to Doctor", key=f"res_{row['rowid']}"):
                    agent_sys.upload_lab_results(row['rowid'], row['patient_name'])
                    st.rerun()


# --- ROLE 6: PHARMACY ---
elif user_role == "Pharmacy (Dispensing)":
    conn = get_db_connection()
    pharm_df = pd.read_sql_query("SELECT rowid, patient_name, queue_number, notes FROM appointments WHERE location='Pharmacy' AND booking_date<=?", conn, params=(target_date,))
    conn.close()
    
    if pharm_df.empty:
        st.info("No pending prescriptions.")
    else:
        for _, row in pharm_df.iterrows():
            with st.expander(f"Patient: {row['patient_name']} ({row['queue_number']})", expanded=True):
                st.markdown(f"**Medical Record & Prescription:** {row['notes']}")
                if st.button("Mark Dispensed & Complete Visit", type="primary", key=f"disp_{row['rowid']}"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("UPDATE appointments SET location='Completed', status='COMPLETED' WHERE rowid=?", (row['rowid'],))
                    conn.commit()
                    conn.close()
                    agent_sys.log(f"[PHARMACY_AGENT] Drugs dispensed to {row['patient_name']}. Cycle complete.")
                    st.rerun()


# --- ROLE 7: SYSTEM TELEMETRY ---
elif user_role == "System Telemetry":
    col1, col2 = st.columns([3, 1])
    with col1:
        log_box = st.container(height=500)
        with log_box:
            if not st.session_state.logs:
                st.caption("Awaiting system events...")
            else:
                for log_msg in reversed(st.session_state.logs):
                    st.markdown(f"`{log_msg}`")
    with col2:
        if st.button("Clear DB & Logs (Reset Demo)", type="primary"):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("DELETE FROM appointments")
            conn.commit()
            conn.close()
            st.session_state.logs = []
            st.rerun()