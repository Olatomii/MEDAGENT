"""Local staff authentication. No default accounts; provisioning requires host access."""
import hashlib
import hmac
import secrets
import time
from .database import connection

ROLES=('admin','front_desk','nurse','physician','pharmacy','ward')
SESSION_SECONDS=8*60*60
ITERATIONS=600_000


class AccessDenied(ValueError):
    pass


def initialize_auth(path):
    with connection(path,write=True) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS staff_users (
            user_id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, role TEXT NOT NULL,
            password_hash TEXT NOT NULL, salt TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS staff_sessions (
            token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES staff_users,
            expires_at REAL NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS login_limits (
            username TEXT PRIMARY KEY, failures INTEGER NOT NULL, blocked_until REAL NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS staff_audit (
            audit_id INTEGER PRIMARY KEY,user_id INTEGER,action TEXT NOT NULL,
            outcome TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')


def password_digest(password,salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),ITERATIONS).hex()


def create_staff(path,username,password,role):
    username=username.strip().lower()
    if not 3<=len(username)<=80 or not all(c.isalnum() or c in '._-' for c in username):
        raise ValueError('Username must be 3–80 letters, digits, dots, underscores or hyphens.')
    if role not in ROLES or not 12<=len(password)<=256:
        raise ValueError('Choose a supported role and a password of 12–256 characters.')
    salt=secrets.token_hex(16)
    digest=password_digest(password,salt)
    with connection(path,write=True) as conn:
        if conn.execute('SELECT 1 FROM staff_users WHERE username=?',(username,)).fetchone():
            raise ValueError('Username already exists.')
        uid=conn.execute('INSERT INTO staff_users(username,role,password_hash,salt) VALUES (?,?,?,?)',
                         (username,role,digest,salt)).lastrowid
        conn.execute("INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,'HOST_CREATE_STAFF','SUCCESS')",(uid,))
        return uid


def login(path,username,password,now=None):
    now=time.time() if now is None else now
    username=username.strip().lower()[:80]
    if len(password)>256:
        raise AccessDenied('Sign-in failed. Check your credentials or try again later.')
    with connection(path,write=True) as conn:
        limit=conn.execute('SELECT * FROM login_limits WHERE username=?',(username,)).fetchone()
        if limit and limit['blocked_until']>now:
            raise AccessDenied('Sign-in failed. Check your credentials or try again later.')
        user=conn.execute('SELECT * FROM staff_users WHERE username=?',(username,)).fetchone()
        salt=user['salt'] if user else '00'*16
        digest=password_digest(password,salt)
        valid=user is not None and user['enabled'] and hmac.compare_digest(digest,user['password_hash'])
        if not valid:
            failures=(limit['failures'] if limit and limit['blocked_until']==0 else 0)+1
            conn.execute('INSERT INTO login_limits VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET failures=excluded.failures,blocked_until=excluded.blocked_until',
                         (username,failures,now+900 if failures>=5 else 0))
            conn.execute("INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,'SIGN_IN','DENIED')",(user['user_id'] if user else None,))
            token=None
        else:
            token=secrets.token_urlsafe(32)
            conn.execute('DELETE FROM login_limits WHERE username=?',(username,))
            conn.execute('DELETE FROM staff_sessions WHERE expires_at<=?',(now,))
            conn.execute('INSERT INTO staff_sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['user_id'],now+SESSION_SECONDS))
            conn.execute("INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,'SIGN_IN','SUCCESS')",(user['user_id'],))
    if token is None:
        raise AccessDenied('Sign-in failed. Check your credentials or try again later.')
    return token


def authenticate(path,token,now=None):
    now=time.time() if now is None else now
    with connection(path) as conn:
        user=conn.execute('''SELECT u.user_id,u.username,u.role FROM staff_sessions s JOIN staff_users u USING(user_id)
            WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1''',
            (hashlib.sha256((token or '').encode()).hexdigest(),now)).fetchone()
    if user is None:
        raise AccessDenied('Your session has ended. Sign in again.')
    return dict(user)


def logout(path,token):
    with connection(path,write=True) as conn:
        digest=hashlib.sha256((token or '').encode()).hexdigest()
        session=conn.execute('SELECT user_id FROM staff_sessions WHERE token_hash=?',(digest,)).fetchone()
        conn.execute('DELETE FROM staff_sessions WHERE token_hash=?',(digest,))
        if session:
            conn.execute("INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,'SIGN_OUT','SUCCESS')",(session['user_id'],))


def disable_staff(path,username):
    with connection(path,write=True) as conn:
        user=conn.execute('SELECT * FROM staff_users WHERE username=?',(username.strip().lower(),)).fetchone()
        if user is None:
            raise ValueError('Staff account not found.')
        if user['role']=='admin' and user['enabled'] and conn.execute("SELECT COUNT(*) FROM staff_users WHERE role='admin' AND enabled=1").fetchone()[0]<=1:
            raise ValueError('Create another administrator before disabling the last enabled administrator.')
        conn.execute('UPDATE staff_users SET enabled=0 WHERE user_id=?',(user['user_id'],))
        conn.execute('DELETE FROM staff_sessions WHERE user_id=?',(user['user_id'],))
        conn.execute("INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,'HOST_DISABLE_STAFF','SUCCESS')",(user['user_id'],))


def reset_password(path,username,password):
    if not 12<=len(password)<=256:
        raise ValueError('Password must contain 12–256 characters.')
    salt=secrets.token_hex(16)
    digest=password_digest(password,salt)
    with connection(path,write=True) as conn:
        user=conn.execute('SELECT user_id FROM staff_users WHERE username=?',(username.strip().lower(),)).fetchone()
        if user is None:
            raise ValueError('Staff account not found.')
        conn.execute('UPDATE staff_users SET password_hash=?,salt=? WHERE user_id=?',(digest,salt,user['user_id']))
        conn.execute('DELETE FROM staff_sessions WHERE user_id=?',(user['user_id'],))
        conn.execute('DELETE FROM login_limits WHERE username=?',(username.strip().lower(),))
        conn.execute("INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,'HOST_RESET_PASSWORD','SUCCESS')",(user['user_id'],))
