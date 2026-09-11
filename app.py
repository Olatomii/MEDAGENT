import sqlite3
import pandas as pd
import streamlit as st
import random
import datetime

# --- 1. DATABASE SETUP (BULLETPROOF INITIALIZATION) ---
DB_NAME = 'medagent_enterprise.db'

def get_db_connection():
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

    c.execute('''CREATE TABLE IF NOT EXISTS waitlist
                 (booking_date TEXT, patient_name TEXT, age INTEGER, gender TEXT, 
                  address TEXT, occupation TEXT, payment_status TEXT, triage_level INTEGER, 
                  specialty TEXT, added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute("SELECT COUNT(*) FROM doctors")
    if c.fetchone()[0] != 9:
        c.execute("DELETE FROM doctors")
        docs = [
            (1, "Dr. Smith", "General Practice"), (2, "Dr. Taylor", "General Practice"),
            (3, "Dr. Jones", "Cardiology"), (4, "Dr. Davis", "Cardiology"),
            (5, "Dr. Brown", "Orthopedics"), (6, "Dr. Wilson", "Orthopedics"),
            (7, "Dr. Evans", "Emergency / Trauma"), (8, "Dr. Carter", "Emergency / Trauma"), (9, "Dr. Mitchell", "Emergency / Trauma")
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
        if triage in [1, 2]:
            spec = "Emergency / Trauma"
            self.log(f"[TRIAGE_AGENT] [ALERT] Level {triage} Emergency. Billing exempt. {name} routed directly to Emergency Ward.", True)
        else:
            if payment != "Cleared":
                self.log(f"[BILLING_AGENT] REJECT: {name} must clear billing before Walk-in/Scheduled consultation.", True)
                return "BILLING_ERROR"

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT doc_id, name FROM doctors WHERE specialty=?", (spec,))
        docs = c.fetchall()
        
        doc_loads = []
        for doc_id, doc_name in docs:
            c.execute("SELECT COUNT(*) FROM appointments WHERE doc_id=? AND booking_date<=? AND status NOT IN ('COMPLETED', 'ABSENT', 'ADMITTED')", (doc_id, date))
            count = c.fetchone()[0]
            doc_loads.append((count, doc_id, doc_name))
            
        doc_loads.sort(key=lambda x: x[0])
        best_load = doc_loads[0][0]
        best_doc_id = doc_loads[0][1]
        best_doc_name = doc_loads[0][2]

        q_num = f"Q-{random.randint(1000, 9999)}"

        if triage in [1, 2]:
            if best_load >= 1:
                self.log(f"[CODE_BLUE_ALERT] Absolute Saturation. All 3 Emergency bays occupied. {name} rejected and diverted.", True)
                conn.close()
                return "CODE_BLUE"
            loc = "Doctor Wait" 
        else:
            if best_load >= 3:
                c.execute("INSERT INTO waitlist (booking_date, patient_name, age, gender, address, occupation, payment_status, triage_level, specialty) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (date, name, age, gender, address, occ, payment, triage, spec))
                conn.commit()
                self.log(f"[WAITLIST_BROKER] {spec} fully saturated at Qmax=3. {name} deferred to pending waitlist.")
                conn.close()
                return "WAITLIST"
            loc = "Nurses Station" 

        c.execute("INSERT INTO appointments (booking_date, doc_id, patient_name, age, gender, address, occupation, payment_status, triage_level, status, location, queue_number, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'WAITING', ?, ?, '')",
                  (date, best_doc_id, name, age, gender, address, occ, payment, triage, loc, q_num))
        conn.commit()
        self.log(f"[ROUTING_AGENT] {name} registered ({q_num}). Assigned to {best_doc_name} (Load: {best_load}). Routed to {loc}.")
        conn.close()
        return "SUCCESS"

    def process_vitals(self, rowid, name, bp_sys, temp, hr, spo2, rr):
        conn = get_db_connection()
        c = conn.cursor()
        
        if bp_sys > 180 or temp > 39.0 or hr > 120 or spo2 < 92 or rr > 24:
            new_note = f"[VITALS]: BP {bp_sys}, HR {hr}, SpO2 {spo2}% [CRITICAL]"
            
            c.execute("SELECT doc_id, name FROM doctors WHERE specialty='Emergency / Trauma'")
            ed_docs = c.fetchall()
            ed_loads = []
            for d in ed_docs:
                c.execute("SELECT COUNT(*) FROM appointments WHERE doc_id=? AND status NOT IN ('COMPLETED', 'ABSENT', 'ADMITTED')", (d[0],))
                ed_loads.append((c.fetchone()[0], d[0], d[1]))
            ed_loads.sort(key=lambda x: x[0])
            
            if ed_loads[0][0] >= 1:
                self.log(f"[CODE_BLUE_ALERT] {name} crashed at Nurses Station, but Emergency Ward is full! Immediate internal diversion executed.", True)
                c.execute("UPDATE appointments SET status='ABSENT', location='Archived', notes = notes || ? WHERE rowid=?", (" | [CRASH - ED FULL]", rowid))
                self.run_vacuum() # Trigger vacuum since a slot opened up
            else:
                best_ed_doc = ed_loads[0][1]
                c.execute("UPDATE appointments SET triage_level=2, doc_id=?, location='Doctor Wait', notes = notes || ? WHERE rowid=?", (best_ed_doc, new_note, rowid))
                self.log(f"[CLINICAL_PREP] [CRITICAL] Vitals breached threshold. {name} autonomously upgraded to L2 and shunted to Emergency Track.", True)
                self.run_vacuum() # Trigger vacuum since the walk-in room is now empty
        else:
            new_note = f"[VITALS]: BP {bp_sys}, HR {hr}, SpO2 {spo2}%"
            c.execute("UPDATE appointments SET location='Doctor Wait', notes = notes || ? WHERE rowid=?", (new_note, rowid))
            self.log(f"[CLINICAL_PREP] Vitals logged for {name}. Cleared for consultation.")
            
        conn.commit()
        conn.close()

    def admit_to_ward(self, rowid, name, age, gender):
        ward = "Children's Ward" if age < 18 else ("Male Ward" if gender == "Male" else "Female Ward")
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE appointments SET location=?, status='ADMITTED' WHERE rowid=?", (ward, rowid))
        conn.commit()
        conn.close()
        self.log(f"[BED_ALLOCATION] {name} processed. Bed locked in {ward}.", True)
        self.run_vacuum() # EVENT TRIGGER: Room is empty, call the Vacuum Agent!
        
    def discharge_from_ward(self, rowid, name, ward):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE appointments SET location='Discharged', status='COMPLETED' WHERE rowid=?", (rowid,))
        conn.commit()
        conn.close()
        self.log(f"[BED_ALLOCATION] {name} medically cleared. Bed released in {ward}.")

    def send_to_lab(self, rowid, name, directive):
        conn = get_db_connection()
        c = conn.cursor()
        new_note = f" | [LAB RQ]: {directive}"
        c.execute("UPDATE appointments SET location='Imaging/Lab', notes = notes || ? WHERE rowid=?", (new_note, rowid))
        conn.commit()
        conn.close()
        self.log(f"[DIAGNOSTIC_AGENT] {name} routed to Imaging/Lab for {directive}.")

    def upload_lab_results(self, rowid, name):
        conn = get_db_connection()
        c = conn.cursor()
        new_note = " | [LAB]: RESULTS READY"
        c.execute("UPDATE appointments SET location='Doctor Wait', notes = notes || ?, triage_level=2 WHERE rowid=?", (new_note, rowid))
        conn.commit()
        conn.close()
        self.log(f"[DIAGNOSTIC_AGENT] Results uploaded for {name}. Patient returned to Doctor Queue.", True)

    def send_to_pharmacy(self, rowid, name, directive):
        conn = get_db_connection()
        c = conn.cursor()
        new_note = f" | [PHARM RQ]: {directive}"
        c.execute("UPDATE appointments SET location='Pharmacy', notes = notes || ? WHERE rowid=?", (new_note, rowid))
        conn.commit()
        conn.close()
        self.log(f"[PHARMACY_AGENT] {name} discharged to Pharmacy. Rx: {directive}.")

    def flag_absent(self, rowid, name, location):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE appointments SET status='ABSENT', location='Archived' WHERE rowid=?", (rowid,))
        conn.commit()
        conn.close()
        self.log(f"[EXCEPTION_AGENT] {name} flagged as ABSENT at {location}. Capacity released.")
        self.run_vacuum() # EVENT TRIGGER: Room is empty, call the Vacuum Agent!

    def run_vacuum(self):
        # The agent now runs silently in the background
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT rowid, booking_date, patient_name, age, gender, address, occupation, payment_status, triage_level, specialty FROM waitlist ORDER BY triage_level ASC, added_time ASC LIMIT 1")
        row = c.fetchone()
        
        if not row:
            # Silent exit if nobody is waiting
            conn.close()
            return
            
        wait_id, date, name, age, gender, address, occ, payment, triage, spec = row
        c.execute("SELECT doc_id, name FROM doctors WHERE specialty=?", (spec,))
        docs = c.fetchall()
        for doc_id, doc_name in docs:
            c.execute("SELECT COUNT(*) FROM appointments WHERE doc_id=? AND booking_date<=? AND status NOT IN ('COMPLETED', 'ABSENT', 'ADMITTED')", (doc_id, date))
            if c.fetchone()[0] < 3: 
                c.execute("DELETE FROM waitlist WHERE rowid=?", (wait_id,))
                q_num = f"Q-{random.randint(1000, 9999)}"
                loc = "Nurses Station"
                c.execute("INSERT INTO appointments (booking_date, doc_id, patient_name, age, gender, address, occupation, payment_status, triage_level, status, location, queue_number, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'WAITING', ?, ?, '')",
                          (date, doc_id, name, age, gender, address, occ, payment, triage, loc, q_num))
                conn.commit()
                # Only logs when it successfully takes autonomous action
                self.log(f"[VACUUM_BROKER] Autonomous Event: Capacity detected. {name} automatically promoted from Waitlist and routed to {doc_name}.", True)
                conn.close()
                return
        conn.close()

    def run_rollover(self, current_date):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT rowid, patient_name, specialty, triage_level FROM appointments WHERE booking_date < ? AND status='WAITING'", (current_date,))
        unserved = c.fetchall()
        if not unserved:
            self.log("[STATE_PERSISTENCE] No unserved backlog found for rollover.")
            conn.close()
            return
        for rowid, name, spec, triage in unserved:
            c.execute("UPDATE appointments SET booking_date=? WHERE rowid=?", (current_date, rowid))
            self.log(f"[STATE_PERSISTENCE] Rolled over unserved patient {name} ({spec}) to {current_date}.", True)
        conn.commit()
        conn.close()


# --- 3. STREAMLIT GUI ---
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

# --- SIDEBAR ---
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
    col1, col2 = st.columns(2)
    p_name = col1.text_input("Full Name")
    p_gender = col2.selectbox("Gender", ["Male", "Female"])
    p_age = col1.number_input("Age", min_value=0, max_value=120, value=30)
    p_occ = col2.text_input("Occupation")
    p_address = st.text_input("Living Address")
    
    st.divider()
    col3, col4 = st.columns(2)
    with col3:
        appointment_date = st.date_input("Schedule Appointment Date", datetime.date.today()).strftime("%Y-%m-%d")
    with col4:
        spec = st.selectbox("Routing Department", ["General Practice", "Cardiology", "Orthopedics", "Emergency / Trauma"])
        
    if spec == "Emergency / Trauma":
        triage_opts = ["1: Resuscitation", "2: Emergent"]
    else:
        triage_opts = ["3: Urgent", "4: Semi-Urgent", "5: Routine"]
        
    triage = st.selectbox("Acuity", triage_opts)
    payment = st.radio("Billing Status", ["Pending", "Cleared"], horizontal=True)
    
    submitted = st.button("Route to Agent Network", type="primary")
    
    if submitted:
        if not p_name:
            st.error("❌ Patient name is required.")
        else:
            result = agent_sys.register_patient(p_name, p_age, p_gender, p_address, p_occ, payment, spec, int(triage[0]), appointment_date)
            
            if result == "BILLING_ERROR":
                st.error(f"❌ System Reject: {p_name} must clear billing status before Walk-in/Scheduled admission.")
            elif result == "CODE_BLUE":
                st.error(f"🚨 Code Blue: Absolute ED Saturation. All 3 Emergency bays are occupied. {p_name} rejected and diverted.")
            elif result == "WAITLIST":
                st.warning(f"⚠️ Capacity Full: {spec} is at maximum load. {p_name} deferred to the pending waitlist.")
            elif result == "SUCCESS":
                st.success(f"✅ {p_name} successfully registered and routed to the network.")


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
                
                c1, c2 = st.columns([3, 1])
                with c1:
                    if st.button("Log Vitals & Trigger Prep Agent", type="primary", key=f"btn_{row['rowid']}", use_container_width=True):
                        agent_sys.process_vitals(row['rowid'], row['patient_name'], bp, temp, hr, spo2, rr)
                        st.rerun()
                with c2:
                    if st.button("Mark Absent", key=f"absent_n_{row['rowid']}", use_container_width=True):
                        agent_sys.flag_absent(row['rowid'], row['patient_name'], "Nurses Station")
                        st.rerun()


# --- ROLE 3: PHYSICIAN ---
elif user_role == "Physician (Consultation)":
    assigned_spec = st.selectbox("Select Physician Specialty Queue:", [
        "General Practice", "Cardiology", "Orthopedics", "Emergency / Trauma"
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
                doc_notes = st.text_input("Clinical Directives:", key=f"doc_notes_{row['rowid']}")
                
                col_a, col_b, col_c, col_d = st.columns(4)
                if col_a.button("Admit to Ward", key=f"admit_{row['rowid']}", use_container_width=True):
                    agent_sys.admit_to_ward(row['rowid'], row['patient_name'], row['age'], row['gender'])
                    st.rerun()
                if col_b.button("Send to Lab", key=f"lab_{row['rowid']}", use_container_width=True):
                    agent_sys.send_to_lab(row['rowid'], row['patient_name'], doc_notes or "Diagnostics")
                    st.rerun()
                if col_c.button("Discharge (Pharm)", type="primary", key=f"pharm_{row['rowid']}", use_container_width=True):
                    agent_sys.send_to_pharmacy(row['rowid'], row['patient_name'], doc_notes or "Dispense")
                    st.rerun()
                if col_d.button("Mark Absent", key=f"absent_d_{row['rowid']}", use_container_width=True):
                    agent_sys.flag_absent(row['rowid'], row['patient_name'], "Physician Queue")
                    st.rerun()


# --- ROLE 4: INPATIENT WARDS ---
elif user_role == "Inpatient Wards":
    conn = get_db_connection()
    wards_df = pd.read_sql_query("SELECT rowid, patient_name, age, gender, location FROM appointments WHERE status='ADMITTED'", conn)
    conn.close()

    if wards_df.empty:
        st.info("No patients currently admitted to the wards.")
    else:
        col1, col2, col3 = st.columns(3)
        for idx, (col, w_name) in enumerate(zip([col1, col2, col3], ['Male Ward', 'Female Ward', "Children's Ward"])):
            with col:
                st.markdown(f"#### {w_name}")
                sub_df = wards_df[wards_df['location'] == w_name]
                for _, row in sub_df.iterrows():
                    with st.container(border=True):
                        st.info(f"{row['patient_name']} (Age: {row['age']})")
                        if st.button("Discharge", key=f"d_{row['rowid']}", use_container_width=True):
                            agent_sys.discharge_from_ward(row['rowid'], row['patient_name'], w_name)
                            st.rerun()


# --- ROLE 5: DIAGNOSTICS ---
elif user_role == "Diagnostics (Imaging/Lab)":
    conn = get_db_connection()
    lab_df = pd.read_sql_query("SELECT rowid, patient_name, queue_number, notes FROM appointments WHERE location='Imaging/Lab' AND booking_date<=?", conn, params=(target_date,))
    conn.close()
    if lab_df.empty:
        st.info("No pending lab requests.")
    else:
        for _, row in lab_df.iterrows():
            with st.expander(f"{row['patient_name']} ({row['queue_number']})", expanded=True):
                st.markdown(f"**Directive:** {row['notes']}")
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
            with st.expander(f"{row['patient_name']} ({row['queue_number']})", expanded=True):
                st.markdown(f"**Prescription:** {row['notes']}")
                if st.button("Dispense & Complete Visit", type="primary", key=f"disp_{row['rowid']}"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("UPDATE appointments SET location='Completed', status='COMPLETED' WHERE rowid=?", (row['rowid'],))
                    conn.commit()
                    conn.close()
                    agent_sys.log(f"[PHARMACY] Visit completed for {row['patient_name']}.")
                    agent_sys.run_vacuum() # EVENT TRIGGER: Room is totally cleared, call the Vacuum Agent!
                    st.rerun()


# --- ROLE 7: SYSTEM TELEMETRY ---
elif user_role == "System Telemetry":
    col1, col2 = st.columns([3, 1])
    with col1:
        log_box = st.container(height=400)
        with log_box:
            if not st.session_state.logs:
                st.caption("Awaiting system events...")
            else:
                for log_msg in reversed(st.session_state.logs):
                    st.markdown(f"`{log_msg}`")
        
        st.divider()
        st.markdown("#### Pending Waitlist (Walk-in Overflow)")
        conn = get_db_connection()
        waitlist_df = pd.read_sql_query("SELECT booking_date, patient_name, triage_level, specialty FROM waitlist", conn)
        conn.close()
        if waitlist_df.empty:
            st.info("No patients currently on the waitlist.")
        else:
            st.dataframe(waitlist_df, use_container_width=True, hide_index=True)
            
    with col2:
        # Kept as a fallback "Force Manual Override" for the demonstration
        if st.button("Force Vacuum Agent (Manual Override)", use_container_width=True):
            agent_sys.run_vacuum()
            st.rerun()
            
        if st.button("Run State Persistence Rollover", use_container_width=True):
            agent_sys.run_rollover(target_date)
            st.rerun()

        st.divider()
        if st.button("Clear DB & Logs (Reset)", type="primary", use_container_width=True):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("DELETE FROM appointments")
            c.execute("DELETE FROM waitlist") 
            conn.commit()
            conn.close()
            st.session_state.logs = []
            st.rerun()