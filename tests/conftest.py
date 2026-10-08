"""Run the same v2 contracts on SQLite or an isolated CI PostgreSQL database."""
import os
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def database_path(tmp_path):
    url = os.getenv('MEDAGENT_TEST_POSTGRES_URL')
    if not url:
        yield str(tmp_path / 'application.db')
        return
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from urllib.parse import urlsplit, urlunsplit
    from hospital.postgres import pool
    name = 'test_medagent_' + uuid.uuid4().hex
    # This fixture is used only with a disposable CI PostgreSQL service.
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    parts = urlsplit(url)
    target = urlunsplit(parts._replace(path='/' + name))
    try:
        yield target
    finally:
        pool(target).close()
        pool.cache_clear()
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
