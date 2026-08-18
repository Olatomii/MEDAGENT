import sqlite3
import pandas as pd
import streamlit as st
import random
import datetime
import plotly.express as px

# --- 1. DATABASE SETUP ---
DB_NAME = 'medagent.db'
MAX_QUEUE = 3  

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS doctors
                 (doc_id INTEGER PRIMARY KEY, name TEXT, specialty TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS appointments
                 (booking_date TEXT, doc_id INTEGER, patient_name TEXT, triage_level INTEGER, 
                  status TEXT, added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS waitlist
                 (target_date TEXT, patient_id INTEGER, patient_name TEXT, specialty TEXT, 
                  triage_level INTEGER, added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute("SELECT COUNT(*) FROM doctors")
    if c.fetchone()[0] < 6:
        c.execute("DELETE FROM doctors")
        docs = [
            (1, "Dr. Smith", "Cardiology"), (2, "Dr. Taylor", "Cardiology"),
            (3, "Dr. Jones", "Orthopedics"), (4, "Dr. Brown", "Orthopedics"),
            (5, "Dr. Adams", "General Practice"), (6, "Dr. Clark", "General Practice")
        ]
        c.executemany("INSERT INTO doctors (doc_id, name, specialty) VALUES (?, ?, ?)", docs)
    conn.commit()
    conn.close()

init_db()

# --- 2. MULTI-AGENT MODELS ---

class Patient:
    def __init__(self, patient_id, name, specialty, triage_level, target_date):
        self.patient_id = patient_id
        self.name = name
        self.specialty = specialty
        self.triage_level = triage_level
        self.target_date = target_date 

class SchedulingAgent:
    def __init__(self):
        if 'logs' not in st.session_state:
            st.session_state.logs = []
    
    def log(self, message):
        st.session_state.logs.append(message)

    def get_triage_name(self, level):
        names = {1: "Level 1 (Resuscitation)", 2: "Level 2 (Emergent)", 3: "Level 3 (Urgent)", 4: "Level 4 (Semi-Urgent)", 5: "Level 5 (Routine)"}
        return names.get(level, "Unknown")

    def book_appointment(self, patient, is_vacuum=False):
        if not is_vacuum:
            triage_name = self.get_triage_name(patient.triage_level)
            self.log(f"[PATIENT_AGENT_{patient.patient_id}] -> ADMISSION REQUEST: {{Specialty: {patient.specialty}, Date: {patient.target_date}, {triage_name}}}")
        
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        c.execute("SELECT doc_id, name FROM doctors WHERE specialty=?", (patient.specialty,))
        raw_matching_docs = c.fetchall()
        
        if not raw_matching_docs:
            self.log(f"[TRIAGE_SYSTEM] -> REJECT: {{No attending physicians found for {patient.specialty}}}")
            conn.close()
            return False

        # --- DYNAMIC LOAD BALANCING ---
        # Sort doctors by their current active patient load to distribute admissions evenly
        doc_loads = []
        for doc_id, doc_name in raw_matching_docs:
            c.execute("SELECT COUNT(*) FROM appointments WHERE doc_id=? AND booking_date=? AND status != 'COMPLETED'", (doc_id, patient.target_date))
            count = c.fetchone()[0]
            doc_loads.append((count, doc_id, doc_name))
        
        doc_loads.sort(key=lambda x: x[0])
        matching_docs = [(d[1], d[2]) for d in doc_loads]

        # --- IMMEDIATE CONSULTATION & INTERRUPTION (Levels 1 & 2 ONLY) ---
        if patient.triage_level in [1, 2]:
            idle_doc = None
            # 1. Attempt to find a physician not currently in consultation
            for doc_id, doc_name in matching_docs:
                c.execute("SELECT status FROM appointments WHERE doc_id=? AND booking_date=? AND status='IN_CONSULTATION'", (doc_id, patient.target_date))
                if not c.fetchone():
                    idle_doc = (doc_id, doc_name)
                    break
            
            if idle_doc:
                doc_id, doc_name = idle_doc
                c.execute("INSERT INTO appointments (booking_date, doc_id, patient_name, triage_level, status) VALUES (?, ?, ?, ?, 'IN_CONSULTATION')",
                          (patient.target_date, doc_id, patient.name, patient.triage_level))
                conn.commit()
                self.log(f"[TRIAGE_SYSTEM] -> ACTION: {{Direct routing to {doc_name} for immediate clinical assessment}}")
                conn.close()
                return True

            # 2. If no physicians are idle, initiate active hijack of a lower-priority case
            hijack_candidate = None
            for doc_id, doc_name in matching_docs:
                c.execute("SELECT rowid, patient_name, triage_level FROM appointments WHERE doc_id=? AND booking_date=? AND status='IN_CONSULTATION'", (doc_id, patient.target_date))
                active_pt = c.fetchone()
                if active_pt:
                    b_rowid, b_p_name, b_t_level = active_pt
                    if b_t_level > patient.triage_level:
                        hijack_candidate = (doc_id, doc_name, b_rowid, b_p_name, b_t_level)
                        break 

            if hijack_candidate:
                doc_id, doc_name, b_rowid, b_p_name, b_t_level = hijack_candidate
                self.log(f"[CRITICAL_OVERRIDE] {patient.name} (L{patient.triage_level}) interrupting {doc_name}'s active consult with {b_p_name} (L{b_t_level})")
                c.execute("UPDATE appointments SET patient_name=?, triage_level=? WHERE rowid=?", (patient.name, patient.triage_level, b_rowid))
                conn.commit()
                self.log(f"[TRIAGE_SYSTEM] -> ACTION: {{Re-triaging displaced patient: {b_p_name}}}")
                bumped_patient = Patient(9999, b_p_name, patient.specialty, b_t_level, patient.target_date)
                conn.close() 
                self.book_appointment(bumped_patient)
                return True
            else:
                # 3. Saturated Trauma Protocol (Level 1 Rejection)
                if patient.triage_level == 1:
                    self.log(f"[CODE_BLUE_ALERT] All {patient.specialty} physicians are saturated with Resuscitation cases. Initiating immediate external hospital transfer for {patient.name}.")
                    conn.close()
                    return False

        # --- STANDARD QUEUE ADMISSION ---
        for doc_id, doc_name in matching_docs:
            c.execute("SELECT status FROM appointments WHERE doc_id=? AND booking_date=? AND status != 'COMPLETED'", (doc_id, patient.target_date))
            active_statuses = [row[0] for row in c.fetchall()]
            
            if len(active_statuses) < MAX_QUEUE:
                new_status = 'WAITING' if 'IN_CONSULTATION' in active_statuses else 'IN_CONSULTATION'
                c.execute("INSERT INTO appointments (booking_date, doc_id, patient_name, triage_level, status) VALUES (?, ?, ?, ?, ?)",
                          (patient.target_date, doc_id, patient.name, patient.triage_level, new_status))
                conn.commit()
                self.log(f"[TRIAGE_SYSTEM] -> PROPOSE: {{Admitted {patient.name} to {doc_name}'s queue as {new_status}}}")
                conn.close()
                return True
        
        # --- WAITLIST TRIAGE OVERRIDE ---
        bump_candidate = None
        highest_triage_num = patient.triage_level
        
        for doc_id, doc_name in matching_docs:
            c.execute("SELECT rowid, patient_name, triage_level FROM appointments WHERE doc_id=? AND booking_date=? AND status='WAITING'", (doc_id, patient.target_date))
            waiters = c.fetchall()
            for rowid, p_name, t_level in waiters:
                if t_level > highest_triage_num: 
                    highest_triage_num = t_level
                    bump_candidate = (doc_id, doc_name, rowid, p_name, t_level)
        
        if bump_candidate:
            b_doc_id, b_doc_name, b_rowid, b_p_name, b_t_level = bump_candidate
            self.log(f"[TRIAGE_SYSTEM] -> PROPOSE: {{Displacing {b_p_name} (L{b_t_level}) from waiting area for {patient.name} (L{patient.triage_level})}}")
            c.execute("UPDATE appointments SET patient_name=?, triage_level=? WHERE rowid=?", (patient.name, patient.triage_level, b_rowid))
            conn.commit()
            self.log(f"[PHYSICIAN_AGENT_{b_doc_name.replace(' ', '_').upper()}] -> ACCEPT: {{Override Successful}}")
            self.log(f"[TRIAGE_SYSTEM] -> ACTION: {{Re-triaging displaced patient: {b_p_name}}}")
            bumped_patient = Patient(9999, b_p_name, patient.specialty, b_t_level, patient.target_date)
            conn.close() 
            self.book_appointment(bumped_patient)
            return True

        # --- FALLBACK TO PENDING WAITLIST ---
        self.log(f"[TRIAGE_SYSTEM] -> REJECT: {{Clinical capacity exceeded for {patient.specialty}. {patient.name} registered to priority waitlist.}}")
        c.execute("INSERT INTO waitlist (target_date, patient_id, patient_name, specialty, triage_level) VALUES (?, ?, ?, ?, ?)",
                  (patient.target_date, patient.patient_id, patient.name, patient.specialty, patient.triage_level))
        conn.commit()
            
        conn.close()
        return False
    
    def vacuum_waitlist(self, specialty, target_date):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT patient_id, patient_name, triage_level FROM waitlist WHERE target_date=? AND specialty=? ORDER BY triage_level ASC, added_time ASC LIMIT 1", (target_date, specialty))
        res = c.fetchone()
        
        if res:
            pid, pname, tlvl = res
            self.log(f"[AUTOMATED_TRIAGE] Clinical capacity detected. Admitting {pname} (L{tlvl}) from Waitlist into Active Queue.")
            c.execute("DELETE FROM waitlist WHERE patient_id=? AND target_date=?", (pid, target_date))
            conn.commit()
            conn.close()
            pulled_patient = Patient(pid, pname, specialty, tlvl, target_date)
            self.book_appointment(pulled_patient, is_vacuum=True)
        else:
            conn.close()

    def complete_consultation(self, doc_id, doc_name, specialty, target_date):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE appointments SET status='COMPLETED' WHERE doc_id=? AND booking_date=? AND status='IN_CONSULTATION'", (doc_id, target_date))
        c.execute("SELECT rowid FROM appointments WHERE doc_id=? AND booking_date=? AND status='WAITING' ORDER BY triage_level ASC, added_time ASC LIMIT 1", (doc_id, target_date))
        nxt = c.fetchone()
        if nxt:
            c.execute("UPDATE appointments SET status='IN_CONSULTATION' WHERE rowid=?", (nxt[0],))
        conn.commit()
        conn.close()
        
        self.log(f"[PHYSICIAN_AGENT_{doc_name.replace(' ', '_').upper()}] -> INFORM: {{Consultation discharged. Available for next admission.}}")
        self.vacuum_waitlist(specialty, target_date)

    def process_eod_waitlist(self, current_date_str):
        self.log(f"[ROLLOVER_PROTOCOL] Initiating End-of-Shift processing for {current_date_str}...")
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT patient_id, patient_name, specialty, triage_level FROM waitlist WHERE target_date=? ORDER BY triage_level ASC", (current_date_str,))
        patients = c.fetchall()
        
        if not patients:
            self.log(f"[ROLLOVER_PROTOCOL] No pending admissions found for {current_date_str}.")
            conn.close()
            return
            
        c.execute("DELETE FROM waitlist WHERE target_date=?", (current_date_str,))
        conn.commit()
        conn.close()
        
        current_date_obj = datetime.datetime.strptime(current_date_str, "%Y-%m-%d").date()
        next_date_str = (current_date_obj + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        for pid, p_name, spec, t_lvl in patients:
            self.log(f"[ROLLOVER_PROTOCOL] Systematically migrating {p_name} (L{t_lvl}) to {next_date_str}")
            rolled_over = Patient(pid, p_name, spec, t_lvl, next_date_str)
            self.book_appointment(rolled_over)


# --- 3. STREAMLIT UI CONFIGURATION ---

st.set_page_config(page_title="MedAgent Sync | Enterprise Clinical Engine", layout="wide")

st.markdown("""
<style>
    .stMetric { background-color: #ffffff !important; padding: 15px !important; border-radius: 6px !important; border: 1px solid #d1d5db !important; }
    .stMetric label { color: #4b5563 !important; font-weight: 600 !important; }
    .stMetric [data-testid="stMetricValue"] { color: #111827 !important; }
</style>
""", unsafe_allow_html=True)

st.title("MedAgent Sync")
st.markdown("**Enterprise Multi-Agent Resource Allocation & Dynamic Triage Engine**")
st.divider()

scheduler = SchedulingAgent()

# Global Metrics
conn = sqlite3.connect(DB_NAME)
global_appts = pd.read_sql_query("SELECT * FROM appointments", conn)
global_waitlist = pd.read_sql_query("SELECT * FROM waitlist", conn)
docs_count_df = pd.read_sql_query("SELECT COUNT(*) as cnt FROM doctors", conn)
conn.close()

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
kpi1.metric("Active Encounters", len(global_appts[global_appts['status'] != 'COMPLETED']))
kpi2.metric("Discharged Consultations", len(global_appts[global_appts['status'] == 'COMPLETED']))
kpi3.metric("Pending Waitlist", len(global_waitlist))
kpi4.metric("Attending Physicians", int(docs_count_df['cnt'].iloc[0]))
st.divider()

# Tabbed Interface
tab_sim, tab_logs, tab_analytics = st.tabs([
    "Clinical Operations", 
    "System Telemetry", 
    "Analytics & Rollover"
])

with tab_sim:
    st.subheader("1. Clinical Triage Dispatcher")
    st.caption("Initiate patient ingress and observe autonomous algorithms dynamically allocate clinical resources.")
    
    col_d1, col_d2, col_d3, col_d4 = st.columns([2, 2, 2, 1])
    with col_d1:
        target_date = st.date_input("Admission Date", datetime.date.today())
    with col_d2:
        triage_choice = st.selectbox("Acuity Level", ["Level 1: Resuscitation", "Level 2: Emergent", "Level 3: Urgent", "Level 4: Semi-Urgent", "Level 5: Routine"], index=4)
        triage_int = int(triage_choice.split(":")[0][-1])
    with col_d3:
        spec = st.selectbox("Target Ward", ["Cardiology", "Orthopedics", "General Practice"])
    with col_d4:
        st.write("") 
        st.write("")
        btn_type = "primary" if triage_int <= 2 else "secondary"
        if st.button("Dispatch", use_container_width=True, type=btn_type):
            pid = random.randint(1000, 9999)
            new_patient = Patient(pid, f"Patient-{pid}", spec, triage_int, target_date.strftime("%Y-%m-%d"))
            scheduler.book_appointment(new_patient)
            st.rerun()

    st.divider()
    st.subheader("2. Live Ward Queues & Physician Load Balancing")
    
    conn = sqlite3.connect(DB_NAME)
    docs_df = pd.read_sql_query("SELECT * FROM doctors", conn)
    appts_df = pd.read_sql_query("SELECT * FROM appointments WHERE booking_date=?", conn, params=(target_date.strftime("%Y-%m-%d"),))
    conn.close()

    col_queue, col_doc = st.columns([2, 1])

    with col_queue:
        labels = {1: "[L1 - Resuscitation]", 2: "[L2 - Emergent]", 3: "[L3 - Urgent]", 4: "[L4 - Semi-Urgent]", 5: "[L5 - Routine]"}
        
        for _, doc in docs_df.iterrows():
            doc_appts = appts_df[appts_df['doc_id'] == doc['doc_id']]
            active = doc_appts[doc_appts['status'] != 'COMPLETED'].sort_values(by=['status', 'triage_level', 'added_time'], ascending=[True, True, True])
            
            with st.expander(f"Dr. {doc['name'].split(' ')[1]} ({doc['specialty']}) | Consultations Discharged: {len(doc_appts[doc_appts['status'] == 'COMPLETED'])}", expanded=True):
                if active.empty:
                    st.write("Ward clear. Physician is awaiting admissions.")
                else:
                    for _, row in active.iterrows():
                        lbl = labels.get(row['triage_level'], "")
                        if row['status'] == 'IN_CONSULTATION':
                            st.success(f"{lbl} {row['patient_name']} - ACTIVE CONSULTATION")
                        else:
                            st.info(f"{lbl} {row['patient_name']} - WAITING ROOM")

    with col_doc:
        st.markdown("#### Clinical Cycle Operations")
        st.caption("Register consultation discharge. The automated logic will instantly process pending admissions from the priority waitlist.")
        
        for _, doc in docs_df.iterrows():
            if st.button(f"Discharge Dr. {doc['name'].split(' ')[1]}'s Active", key=f"comp_{doc['doc_id']}", use_container_width=True):
                scheduler.complete_consultation(int(doc['doc_id']), doc['name'], doc['specialty'], target_date.strftime("%Y-%m-%d"))
                st.rerun()
                
        st.divider()
        if st.button("Purge Clinical Database", use_container_width=True):
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("DELETE FROM appointments")
            c.execute("DELETE FROM waitlist")
            conn.commit()
            conn.close()
            st.session_state.logs = []
            st.rerun()

with tab_logs:
    st.subheader("System Telemetry & Event Audit")
    st.caption("Real-time stream of agent negotiations, triage overrides, and automated logic protocols.")
    
    log_box = st.container()
    with log_box:
        if not st.session_state.logs:
            st.write("Awaiting autonomous system events...")
        else:
            log_html = "<div id='logarea' style='height:500px; overflow:auto; padding:8px; border:1px solid #e6e6e6; background:#ffffff; border-radius:6px;'>"
            for log_msg in reversed(st.session_state.logs):
                if "CRITICAL" in log_msg or "CODE_BLUE" in log_msg:
                    style = "color:#721c24;background:#f8d7da;padding:8px;margin-bottom:6px;border-radius:4px;"
                elif "REJECT" in log_msg or "ALERT" in log_msg:
                    style = "color:#721c24;background:#f8d7da;padding:8px;margin-bottom:6px;border-radius:4px;"
                elif "PROPOSE" in log_msg or "ACTION" in log_msg or "migrating" in log_msg:
                    style = "color:#856404;background:#fff3cd;padding:8px;margin-bottom:6px;border-radius:4px;"
                elif "AUTOMATED_TRIAGE" in log_msg:
                    style = "color:#0c5460;background:#d1ecf1;padding:8px;margin-bottom:6px;border-radius:4px;"
                elif "ACCEPT" in log_msg or "discharged" in log_msg or "INFORM" in log_msg:
                    style = "color:#155724;background:#d4edda;padding:8px;margin-bottom:6px;border-radius:4px;"
                else:
                    style = "color:#0c5460;background:#e2f0f9;padding:8px;margin-bottom:6px;border-radius:4px;"
                log_html += f"<div style='{style}'>" + log_msg + "</div>\n"
            log_html += "</div><script>var e=document.getElementById('logarea'); if(e){ e.scrollTop = e.scrollHeight; }</script>"
            st.markdown(log_html, unsafe_allow_html=True)
            
        if st.button("Clear Telemetry Data", use_container_width=True):
            st.session_state.logs = []
            st.rerun()

with tab_analytics:
    st.subheader("System Analytics & Waitlist Management")
    
    col_a1, col_a2 = st.columns(2)
    
    with col_a1:
        st.markdown("#### Acuity & Specialty Distribution")
        conn = sqlite3.connect(DB_NAME)
        chart_df = pd.read_sql_query("SELECT d.specialty, COUNT(*) as count FROM appointments a JOIN doctors d ON a.doc_id = d.doc_id GROUP BY d.specialty", conn)
        conn.close()
        
        if not chart_df.empty:
            fig = px.pie(chart_df, names='specialty', values='count', hole=0.4, title="Active Admissions by Ward")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Insufficient data for graphical distribution.")

    with col_a2:
        st.markdown("#### Shift Transition & Rollover Protocol")
        st.caption("Execute end-of-shift processing to systematically migrate unallocated triage patients to the next operational cycle.")
        rollover_date = st.date_input("Select Transition Date:", datetime.date.today(), key="eod_cal")
        
        if st.button("Execute Rollover Protocol", use_container_width=True):
            scheduler.process_eod_waitlist(rollover_date.strftime("%Y-%m-%d"))
            st.rerun()

    st.divider()
    st.markdown("#### Clinical Priority Waitlist Register")
    conn = sqlite3.connect(DB_NAME)
    waitlist_df = pd.read_sql_query("SELECT target_date as 'Date', patient_name as 'Patient', specialty as 'Target Ward', triage_level as 'Priority Level' FROM waitlist ORDER BY target_date ASC, triage_level ASC", conn)
    conn.close()

    if not waitlist_df.empty:
        st.warning("Unallocated patients pending resource availability or shift transition.")
        st.dataframe(waitlist_df, use_container_width=True)
    else:
        st.info("Waitlist status: Clear. All patient vectors successfully admitted.")