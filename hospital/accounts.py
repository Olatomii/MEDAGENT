"""Account administration and patient invitations, always checked server-side."""
import hashlib
import secrets
import time
from .database import connection
from .auth import authenticate,AccessDenied,ROLES,password_digest


def require_user(path,token,roles):
    user=authenticate(path,token)
    if user['must_change'] or user['role'] not in roles:
        raise AccessDenied('This account cannot perform that action.')
    return user


def audit(conn,user,action,target):
    conn.execute('INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,?,?)',
                 (user['user_id'],action,'SUCCESS; target='+str(target)))


def list_accounts(path,token):
    require_user(path,token,{'admin'})
    with connection(path) as conn:
        return [dict(r) for r in conn.execute('SELECT user_id,username,role,enabled,must_change,patient_id FROM staff_users ORDER BY username')]


def add_staff(path,token,username,password,role):
    user=require_user(path,token,{'admin'})
    username=username.strip().lower()
    if role not in ROLES or not 3<=len(username)<=80 or not all(c.isalnum() or c in '._-' for c in username):
        raise ValueError('Choose a staff role and a username of 3–80 letters, digits, dots, underscores or hyphens.')
    if not 12<=len(password)<=256:
        raise ValueError('Temporary password must be 12–256 characters.')
    with connection(path,write=True) as conn:
        if conn.execute('SELECT 1 FROM staff_users WHERE username=?',(username,)).fetchone():
            raise ValueError('Username is already taken.')
        salt=secrets.token_hex(16)
        uid=conn.execute('INSERT INTO staff_users(username,role,salt,password_hash,must_change) VALUES (?,?,?,?,1)',
                         (username,role,salt,password_digest(password,salt))).lastrowid
        audit(conn,user,'CREATE_STAFF',uid)
    return uid


def manage_account(path,token,user_id,role,enabled):
    user=require_user(path,token,{'admin'})
    with connection(path,write=True) as conn:
        target=conn.execute('SELECT * FROM staff_users WHERE user_id=?',(user_id,)).fetchone()
        if target is None or role not in ROLES+('patient',):
            raise ValueError('Account or role not found.')
        if (target['role']=='patient') != (role=='patient'):
            raise ValueError('Patient identities cannot be converted to staff accounts or vice versa.')
        if user_id==user['user_id'] and (not enabled or role!='admin'):
            raise ValueError('Use another administrator to change your own administrative access.')
        if target['role']=='admin' and target['enabled'] and (role!='admin' or not enabled):
            if conn.execute("SELECT COUNT(*) FROM staff_users WHERE role='admin' AND enabled=1").fetchone()[0]<=1:
                raise ValueError('Keep at least one enabled administrator.')
        conn.execute('UPDATE staff_users SET role=?,enabled=? WHERE user_id=?',(role,int(enabled),user_id))
        conn.execute('DELETE FROM staff_sessions WHERE user_id=?',(user_id,))
        audit(conn,user,'UPDATE_ACCESS',user_id)


def reset_account(path,token,user_id,password):
    user=require_user(path,token,{'admin'})
    if not 12<=len(password)<=256:
        raise ValueError('Temporary password must be 12–256 characters.')
    with connection(path,write=True) as conn:
        target=conn.execute('SELECT username FROM staff_users WHERE user_id=?',(user_id,)).fetchone()
        if target is None:
            raise ValueError('Account not found.')
        salt=secrets.token_hex(16)
        conn.execute('UPDATE staff_users SET salt=?,password_hash=?,must_change=1 WHERE user_id=?',
                     (salt,password_digest(password,salt),user_id))
        conn.execute('DELETE FROM staff_sessions WHERE user_id=?',(user_id,))
        conn.execute('DELETE FROM login_limits WHERE username=?',(target['username'],))
        audit(conn,user,'RESET_ACCOUNT_PASSWORD',user_id)


def invite_patient(path,token,patient_id):
    user=require_user(path,token,{'admin','front_desk'})
    code=secrets.token_urlsafe(32)
    with connection(path,write=True) as conn:
        if not conn.execute('SELECT 1 FROM patients WHERE patient_id=?',(patient_id,)).fetchone():
            raise ValueError('Patient not found.')
        if conn.execute('SELECT 1 FROM staff_users WHERE patient_id=?',(patient_id,)).fetchone():
            raise ValueError('This patient already has an account. An administrator can reset access.')
        conn.execute('DELETE FROM patient_invites WHERE patient_id=?',(patient_id,))
        conn.execute('INSERT INTO patient_invites(token_hash,patient_id,expires_at) VALUES (?,?,?)',
                     (hashlib.sha256(code.encode()).hexdigest(),patient_id,time.time()+48*3600))
        audit(conn,user,'INVITE_PATIENT',patient_id)
    return code


def accept_invitation(path,code,username,password):
    username=username.strip().lower()
    if not 3<=len(username)<=80 or not all(c.isalnum() or c in '._-' for c in username) or not 12<=len(password)<=256:
        raise ValueError('Use a valid username and a password of 12–256 characters.')
    with connection(path,write=True) as conn:
        invite=conn.execute('SELECT * FROM patient_invites WHERE token_hash=? AND consumed=0 AND expires_at>?',
                            (hashlib.sha256(code.encode()).hexdigest(),time.time())).fetchone()
        if invite is None:
            raise AccessDenied('Invitation is invalid or expired.')
        if conn.execute('SELECT 1 FROM staff_users WHERE username=? OR patient_id=?',(username,invite['patient_id'])).fetchone():
            raise ValueError('Username or patient account already exists.')
        salt=secrets.token_hex(16)
        uid=conn.execute("INSERT INTO staff_users(username,role,password_hash,salt,patient_id) VALUES (?,'patient',?,?,?)",
                         (username,password_digest(password,salt),salt,invite['patient_id'])).lastrowid
        conn.execute('UPDATE patient_invites SET consumed=1 WHERE token_hash=?',(invite['token_hash'],))
        conn.execute("INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,'ACCEPT_PATIENT_INVITE','SUCCESS')",(uid,))
