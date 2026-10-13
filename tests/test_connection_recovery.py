"""Exercise idle-connection recovery only against the disposable CI database."""
import pytest

from hospital.database import is_postgres
from hospital.service import Hospital
from hospital.auth import initialize_auth, create_staff, login, authenticate


def test_admin_login_recovers_after_idle_socket_is_closed(database_path):
    if not is_postgres(database_path):
        pytest.skip('Requires disposable PostgreSQL service')
    import psycopg
    from hospital.postgres import pool

    hospital = Hospital(database_path)
    initialize_auth(database_path)
    create_staff(database_path, 'recovery-admin', 'Synthetic-test-password-123', 'admin')
    with pool(database_path).connection() as raw:
        stale_pid = raw.execute('SELECT pg_backend_pid()').fetchone()[0]
    # Close only this test's idle backend, without notifying the client pool.
    # This reproduces a hosted database closing an idle connection remotely.
    with psycopg.connect(database_path, autocommit=True) as control:
        assert control.execute('SELECT pg_terminate_backend(%s)', (stale_pid,)).fetchone()[0]

    assert hospital.records('SELECT user_id FROM staff_users LIMIT 1')
    token = login(database_path, 'recovery-admin', 'Synthetic-test-password-123')
    assert authenticate(database_path, token)['role'] == 'admin'
