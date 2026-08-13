import sqlite3
import pandas as pd
import streamlit as st
import random
import datetime

# --- 1. DATABASE SETUP (Expanded Capacity & Dynamic Queues) ---
DB_NAME = 'medagent.db'
MAX_QUEUE = 3  

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Smart Upgrade: Ensure tables match the final schema
    try:
        c.execute("SELECT status FROM appointments LIMIT 1")
        c.execute("SELECT COUNT(*) FROM doctors")
        if c.fetchone()[0] < 6:
            raise sqlite3.OperationalError
    except sqlite3.OperationalError:
        c.execute("DROP TABLE IF EXISTS appointments")
        c.execute("DROP TABLE IF EXISTS waitlist")
        c.execute("DROP TABLE IF EXISTS doctors")

    c.execute('''CREATE TABLE IF NOT EXISTS doctors
                 (doc_id INTEGER PRIMARY KEY, name TEXT, specialty TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS appointments
                 (booking_date TEXT, doc_id INTEGER, patient_name TEXT, triage_level INTEGER, 
                  status TEXT, added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS waitlist
                 (target_date TEXT, patient_id INTEGER, patient_name TEXT, specialty TEXT, 
                  triage_level INTEGER, added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute("SELECT COUNT(*) FROM doctors")
    if c.fetchone()[0] == 0:
        docs = [
            (1, "Dr. Smith", "Cardiology"), 
            (2, "Dr. Taylor", "Cardiology"),
            (3, "Dr. Jones", "Orthopedics"), 
            (4, "Dr. Brown", "Orthopedics"),
            (5, "Dr. Adams", "General Practice"),
            (6, "Dr. Clark", "General Practice")
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
        names = {1: "L1 (Resuscitation)", 2: "L2 (Emergent)", 3: "L3 (Urgent)", 4: "L4 (Semi-Urgent)", 5: "L5 (Routine)"}
        return names.get(level, "Unknown")

    def book_appointment(self, patient, is_vacuum=False):
        if not is_vacuum:
            triage_name = self.get_triage_name(patient.triage_level)
            self.log(f"[PATIENT_AGENT_{patient.patient_id}] -> REQUEST: {{Spec: {patient.specialty}, Date: {patient.target_date}, {triage_name}}}")
        
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        c.execute("SELECT doc_id, name FROM doctors WHERE specialty=?", (patient.specialty,))
        matching_docs = c.fetchall()
        
        if not matching_docs:
            self.log(f"[SCHEDULING_AGENT] -> REJECT: {{No Resource Agents found for {patient.specialty}}}")
            conn.close()
            return False

        # --- ATTEMPT 1: "Code Blue" Interruption Protocol (Level 1 ONLY) ---
        if patient.triage_level == 1:
            idle_doc = None
            for doc_id, doc_name in matching_docs:
                c.execute("SELECT status FROM appointments WHERE doc_id=? AND booking_date=? AND status='IN_CONSULTATION'", (doc_id, patient.target_date))
                if not c.fetchone():
                    idle_doc = (doc_id, doc_name)
                    break
            
            if not idle_doc:
                for doc_id, doc_name in matching_docs:
                    c.execute("SELECT rowid, patient_name, triage_level FROM appointments WHERE doc_id=? AND booking_date=? AND status='IN_CONSULTATION'", (doc_id, patient.target_date))
                    active_pt = c.fetchone()
                    
                    if active_pt:
                        b_rowid, b_p_name, b_t_level = active_pt
                        if b_t_level >= 3:
                            self.log(f"[CODE_BLUE] 🚨 L1 Resuscitation INTERRUPTING {doc_name}'s active consultation with {b_p_name} (L{b_t_level})!")
                            c.execute("UPDATE appointments SET patient_name=?, triage_level=? WHERE rowid=?", (patient.name, patient.triage_level, b_rowid))
                            conn.commit()
                            self.log(f"[RESOURCE_AGENT_{doc_name.replace(' ', '_').upper()}] -> ACCEPT: {{Consultation Hijacked}}")
                            self.log(f"[SCHEDULING_AGENT] -> ACTION: {{Re-routing interrupted patient: {b_p_name}}}")
                            bumped_patient = Patient(9999, b_p_name, patient.specialty, b_t_level, patient.target_date)
                            conn.close() 
                            self.book_appointment(bumped_patient)
                            return True

        # --- ATTEMPT 2: Standard Queue Insertion ---
        for doc_id, doc_name in matching_docs:
            c.execute("SELECT status FROM appointments WHERE doc_id=? AND booking_date=? AND status != 'COMPLETED'", (doc_id, patient.target_date))
            active_patients = [row[0] for row in c.fetchall()]
            
            if len(active_patients) < MAX_QUEUE:
                new_status = 'WAITING' if 'IN_CONSULTATION' in active_patients else 'IN_CONSULTATION'
                c.execute("INSERT INTO appointments (booking_date, doc_id, patient_name, triage_level, status) VALUES (?, ?, ?, ?, ?)",
                          (patient.target_date, doc_id, patient.name, patient.triage_level, new_status))
                conn.commit()
                self.log(f"[SCHEDULING_AGENT] -> PROPOSE: {{Assign {patient.name} to {doc_name} as {new_status}}}")
                self.log(f"[RESOURCE_AGENT_{doc_name.replace(' ', '_').upper()}] -> ACCEPT: {{Added to Queue}}")
                conn.close()
                return True
        
        # --- ATTEMPT 3: Triage Override (Bump someone WAITING) ---
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
            self.log(f"[SCHEDULING_AGENT] -> PROPOSE: {{Bump {b_p_name} (L{b_t_level}) from WAITING for {patient.name} (L{patient.triage_level})}}")
            c.execute("UPDATE appointments SET patient_name=?, triage_level=? WHERE rowid=?", (patient.name, patient.triage_level, b_rowid))
            conn.commit()
            self.log(f"[RESOURCE_AGENT_{b_doc_name.replace(' ', '_').upper()}] -> ACCEPT: {{Override Successful}}")
            self.log(f"[SCHEDULING_AGENT] -> ACTION: {{Re-routing bumped patient: {b_p_name}}}")
            bumped_patient = Patient(9999, b_p_name, patient.specialty, b_t_level, patient.target_date)
            conn.close() 
            self.book_appointment(bumped_patient)
            return True

        # --- ATTEMPT 4: Transfer or Waitlist ---
        if patient.triage_level == 1:
            self.log(f"[CRITICAL_ALERT] 🚨 Level 1 Resuscitation ({patient.name}) CANNOT BE ACCOMMODATED. Initiate emergency transfer!")
        else:
            self.log(f"[SCHEDULING_AGENT] -> REJECT: {{All {patient.specialty} Queues full on {patient.target_date}. {patient.name} added to Waitlist.}}")
            c.execute("INSERT INTO waitlist (target_date, patient_id, patient_name, specialty, triage_level) VALUES (?, ?, ?, ?, ?)",
                      (patient.target_date, patient.patient_id, patient.name, patient.specialty, patient.triage_level))
            conn.commit()
            
        conn.close()
        return False
    
    # --- PROTOCOL: Vacuum Waiting List ---
    def vacuum_waitlist(self, specialty, target_date):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT patient_id, patient_name, triage_level FROM waitlist WHERE target_date=? AND specialty=? ORDER BY triage_level ASC, added_time ASC LIMIT 1", (target_date, specialty))
        res = c.fetchone()
        
        if res:
            pid, pname, tlvl = res
            self.log(f"[VACUUM_AGENT] 🌀 Capacity detected! Pulling {pname} (L{tlvl}) from Waitlist into Active Queue.")
            c.execute("DELETE FROM waitlist WHERE patient_id=? AND target_date=?", (pid, target_date))
            conn.commit()
            conn.close()
            pulled_patient = Patient(pid, pname, specialty, tlvl, target_date)
            self.book_appointment(pulled_patient, is_vacuum=True)
        else:
            conn.close()

    # --- PROTOCOL: Complete Consultation ---
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
        
        self.log(f"[RESOURCE_AGENT_{doc_name.replace(' ', '_').upper()}] -> INFORM: {{Consultation Completed. Available for next.}}")
        self.vacuum_waitlist(specialty, target_date)

    # --- PROTOCOL: End Of Day Auto-Scheduler ---
    def process_eod_waitlist(self, current_date_str):
        self.log(f"[EOD_AGENT] 🌙 Initiating End-of-Day processing for {current_date_str}...")
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT patient_id, patient_name, specialty, triage_level FROM waitlist WHERE target_date=? ORDER BY triage_level ASC", (current_date_str,))
        patients = c.fetchall()
        
        if not patients:
            self.log(f"[EOD_AGENT] ✅ No waitlisted patients found for {current_date_str}.")
            conn.close()
            return
            
        c.execute("DELETE FROM waitlist WHERE target_date=?", (current_date_str,))
        conn.commit()
        conn.close()
        
        current_date_obj = datetime.datetime.strptime(current_date_str, "%Y-%m-%d").date()
        next_date_str = (current_date_obj + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        for pid, p_name, spec, t_lvl in patients:
            self.log(f"[EOD_AGENT] ➡️ Rolling over {p_name} (L{t_lvl}) to {next_date_str}")
            rolled_over = Patient(pid, p_name, spec, t_lvl, next_date_str)
            self.book_appointment(rolled_over)


# --- 3. STREAMLIT UI ---

st.set_page_config(page_title="MedAgent Sync Ultimate", layout="wide")
st.title("🏥 MedAgent Sync (Ultimate Queue Edition)")

scheduler = SchedulingAgent()

col1, col2 = st.columns([1, 2])

with col1:
    st.write("### 🎛️ Agent Dispatcher")
    target_date = st.date_input("🗓️ Select Target Date", datetime.date.today())
    target_date_str = target_date.strftime("%Y-%m-%d")

    triage_choice = st.selectbox("Assign Priority", ["Level 1: Resuscitation", "Level 2: Emergent", "Level 3: Urgent", "Level 4: Semi-Urgent", "Level 5: Routine"], index=4)
    triage_int = int(triage_choice.split(":")[0][-1])
    
    btn_type = "primary" if triage_int <= 2 else "secondary"
    if st.button("🚀 Dispatch Patient Agent", use_container_width=True, type=btn_type):
        spec = random.choice(["Cardiology", "Orthopedics", "General Practice"])
        pid = random.randint(1000, 9999)
        new_patient = Patient(pid, f"Pat-{pid}", spec, triage_int, target_date_str)
        scheduler.book_appointment(new_patient)

    st.divider()
    if st.button("🗑️ Reset Entire Database"):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("DELETE FROM appointments")
        c.execute("DELETE FROM waitlist")
        conn.commit()
        conn.close()
        st.session_state.logs = []
        st.rerun()

with col2:
    st.write("### 📡 FIPA-ACL Negotiation Logs")
    log_box = st.container(height=300)
    with log_box:
        if not st.session_state.logs:
            st.write("_Waiting for autonomous agent communications..._")
        for log_msg in reversed(st.session_state.logs):
            if "CRITICAL" in log_msg or "CODE_BLUE" in log_msg: st.error(log_msg, icon="🚨")
            elif "REJECT" in log_msg or "ALERT" in log_msg: st.error(log_msg, icon="🛑")
            elif "PROPOSE" in log_msg or "ACTION" in log_msg or "Rolling" in log_msg: st.warning(log_msg, icon="🔄")
            elif "VACUUM" in log_msg: st.info(log_msg, icon="🌀")
            elif "ACCEPT" in log_msg or "✅" in log_msg or "INFORM" in log_msg: st.success(log_msg, icon="✅")
            else: st.info(log_msg, icon="📩")

st.divider()

# --- 4. LIVE QUEUES & DOCTOR CONTROL PANEL ---
col3, col4 = st.columns([2, 1])
with col3:
    st.write("### 👥 Live Dynamic Queues")
with col4:
    view_date = st.date_input("📅 View Queues For:", datetime.date.today(), key="view_cal")
    view_date_str = view_date.strftime("%Y-%m-%d")

conn = sqlite3.connect(DB_NAME)
docs_df = pd.read_sql_query("SELECT * FROM doctors", conn)
appts_df = pd.read_sql_query("SELECT * FROM appointments WHERE booking_date=?", conn, params=(view_date_str,))
conn.close()

col_queue, col_doc = st.columns([2, 1])

with col_queue:
    icons = {1: "🔴 [L1]", 2: "🟠 [L2]", 3: "🟡 [L3]", 4: "🔵 [L4]", 5: "🟢 [L5]"}
    
    for _, doc in docs_df.iterrows():
        doc_appts = appts_df[appts_df['doc_id'] == doc['doc_id']]
        active = doc_appts[doc_appts['status'] != 'COMPLETED'].sort_values(by=['status', 'triage_level', 'added_time'], ascending=[True, True, True])
        completed_count = len(doc_appts[doc_appts['status'] == 'COMPLETED'])
        
        with st.expander(f"🩺 {doc['name']} ({doc['specialty']}) - {completed_count} Patients Completed Today", expanded=False):
            if active.empty:
                st.write("✅ _No active queue. Doctor is idle._")
            else:
                for _, row in active.iterrows():
                    icon = icons.get(row['triage_level'], "")
                    if row['status'] == 'IN_CONSULTATION':
                        st.success(f"**{icon} {row['patient_name']}** - 🩺 IN CONSULTATION")
                    else:
                        st.info(f"{icon} {row['patient_name']} - 🪑 WAITING")

with col_doc:
    st.write("### 👨‍⚕️ Doctor Dashboard")
    st.caption("Simulate a doctor finishing early.")
    doc_options = {f"{row['name']} ({row['specialty']})": (row['doc_id'], row['name'], row['specialty']) for _, row in docs_df.iterrows()}
    selected_doc_label = st.selectbox("Select Doctor", list(doc_options.keys()))
    
    if st.button("✅ Complete Current Consultation", use_container_width=True, type="primary"):
        d_id, d_name, d_spec = doc_options[selected_doc_label]
        scheduler.complete_consultation(d_id, d_name, d_spec, view_date_str)
        st.rerun()

# --- 5. WAITLIST & EOD DASHBOARD ---
st.divider()
col5, col6 = st.columns([2, 1])

with col5:
    st.write("### 📋 Priority Waitlist (Unallocated)")
with col6:
    if st.button(f"🌙 Run EOD Processing for {view_date_str}", use_container_width=True):
        scheduler.process_eod_waitlist(view_date_str)
        st.rerun()

conn = sqlite3.connect(DB_NAME)
waitlist_df = pd.read_sql_query("SELECT target_date as 'Date', patient_name as 'Patient', specialty as 'Specialty', triage_level as 'Triage Level' FROM waitlist ORDER BY target_date ASC, triage_level ASC", conn)
conn.close()

if not waitlist_df.empty:
    st.warning("Patients pending capacity or End-of-Day rollover.")
    st.dataframe(waitlist_df, use_container_width=True)
else:
    st.info("✅ The waitlist is currently empty.")