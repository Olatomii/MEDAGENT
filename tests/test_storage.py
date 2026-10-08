import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from hospital.auth import AccessDenied, bootstrap_admin, initialize_auth, login
from hospital.database import configured_database, is_postgres
from hospital.service import Hospital
from hospital.postgres import authorize_read, placeholders, translate

PASSWORD = 'Synthetic-persistence-test-42'


def test_remote_configuration_never_falls_back(monkeypatch):
    monkeypatch.setenv('MEDAGENT_V2_DB_PATH', '/tmp/local-only.db')
    monkeypatch.setenv('MEDAGENT_DATABASE_URL', 'postgresql://example/db')
    assert configured_database() == 'postgresql://example/db'
    monkeypatch.setenv('MEDAGENT_DATABASE_URL', 'not-a-database-url')
    with pytest.raises(ValueError): configured_database()


def test_parameters_preserve_quoted_question_marks():
    query = "SELECT '?' AS label FROM patients WHERE name=? AND email LIKE ?"
    assert placeholders(query) == "SELECT '?' AS label FROM patients WHERE name=%s AND email LIKE %s"
    assert placeholders(query, numbered=True).endswith('name=$1 AND email LIKE $2')
    assert "table_name='patients'" in translate('PRAGMA table_info(patients)')


@pytest.mark.parametrize('query', [
    'SELECT * FROM staff_users',
    'SELECT * FROM patients p JOIN staff_users u ON true',
    'SELECT * FROM patients UNION SELECT * FROM staff_users',
    'SELECT * FROM information_schema.tables',
    "SELECT pg_read_file('/etc/passwd')", 'SELECT pg_sleep(5)',
    "SELECT set_config('role','neondb_owner',true)",
    'SELECT * INTO copied FROM patients',
    'SELECT * FROM patients; DELETE FROM patients',
    'WITH changed AS (DELETE FROM patients RETURNING *) SELECT * FROM changed',
    "SELECT 'payload'::custom_type", 'SELECT * FROM patients FOR UPDATE',
    'SELECT public.count(*) FROM patients',
])
def test_postgres_read_boundary_rejects_unsafe_queries(query):
    with pytest.raises(AccessDenied): authorize_read(query, {'patients'})


def test_postgres_read_boundary_accepts_product_queries():
    authorize_read('SELECT p.name,COUNT(a.appointment_id) AS n FROM patients p LEFT JOIN appointments a USING(patient_id) WHERE a.status=? GROUP BY p.name ORDER BY n', {'patients', 'appointments'})


def test_initial_admin_creation_is_serialized(database_path, monkeypatch):
    h = Hospital(database_path)
    initialize_auth(h.path)
    code = 'test-only-bootstrap-code-12345678901234567890'
    monkeypatch.setenv('MEDAGENT_SETUP_TOKEN', code)
    def create(name):
        try:
            bootstrap_admin(h.path, code, name, PASSWORD)
            return True
        except AccessDenied:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ['owner-one', 'owner-two']))
    assert results.count(True) == 1
    assert len(h.records('SELECT user_id FROM staff_users')) == 1


def test_account_and_patient_survive_a_new_process(database_path, monkeypatch):
    h = Hospital(database_path)
    initialize_auth(h.path)
    code = 'test-only-bootstrap-code-12345678901234567890'
    monkeypatch.setenv('MEDAGENT_SETUP_TOKEN', code)
    bootstrap_admin(h.path, code, 'owner', PASSWORD)
    patient = h.create_patient('Synthetic persistence patient', 25, 'Female')
    payload = json.dumps({'database': h.path, 'password': PASSWORD, 'patient': patient})
    # A separate interpreter initializes the app again and signs in with the
    # original account. The URL travels over stdin, never the process arguments.
    result = subprocess.run([sys.executable, '-c', '''
import json,sys
from hospital.service import Hospital
from hospital.auth import initialize_auth,login,authenticate
x=json.load(sys.stdin)
h=Hospital(x['database'])
initialize_auth(h.path)
assert authenticate(h.path,login(h.path,'owner',x['password']))['role']=='admin'
assert h.records('SELECT patient_id FROM patients')[0]['patient_id']==x['patient']
print('persisted')
'''], input=payload, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, 'Account persistence subprocess failed.'
    assert result.stdout.strip() == 'persisted'
