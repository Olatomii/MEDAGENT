import datetime as dt
import os
import sqlite3
import sys
import threading
import pytest
from hospital.service import Hospital
from hospital.auth import initialize_auth,create_staff,login,authenticate,AccessDenied
from hospital.database import connection
from hospital.backup import restore
from hospital.maintenance import daily_backup,run_once
from hospital.runtime import configuration,supervise

@pytest.fixture
def db(tmp_path):
    h=Hospital(str(tmp_path/'hospital.db'))
    initialize_auth(h.path)
    return h


def test_daily_backup_restarts_rotation_and_restore(db,tmp_path):
    create_staff(db.path,'owner','Synthetic-password-42','admin')
    token=login(db.path,'owner','Synthetic-password-42')
    pid=db.create_patient('Synthetic recovery patient',20,'Female')
    directory=tmp_path/'backups'
    now=dt.datetime(2026,9,19,12,tzinfo=dt.timezone.utc)
    first=daily_backup(db.path,directory,now,keep=2)
    initial=first.stat().st_mtime_ns
    assert daily_backup(db.path,directory,now,keep=2).stat().st_mtime_ns==initial
    other=directory/'personal-file.txt';other.write_text('retain')
    daily_backup(db.path,directory,now+dt.timedelta(days=1),keep=2)
    latest=daily_backup(db.path,directory,now+dt.timedelta(days=2),keep=2)
    assert not first.exists() and other.exists()
    assert len(list(directory.glob('*.db')))==2
    assert latest.stat().st_mode & 0o777==0o600
    recovered=restore(latest,tmp_path/'restored.db')
    h=Hospital(str(recovered))
    assert h.records('SELECT patient_id FROM patients')[0]['patient_id']==pid
    with pytest.raises(AccessDenied): authenticate(str(recovered),token)
    assert login(str(recovered),'owner','Synthetic-password-42')


def test_failed_backup_keeps_previous_copies(db,tmp_path,monkeypatch):
    directory=tmp_path/'backups'
    now=dt.datetime(2026,9,19,tzinfo=dt.timezone.utc)
    first=daily_backup(db.path,directory,now)
    def fail(*args): raise OSError('sensitive failure details')
    monkeypatch.setattr('hospital.maintenance.snapshot',fail)
    with pytest.raises(OSError): daily_backup(db.path,directory,now+dt.timedelta(days=1))
    assert first.exists() and len(list(directory.glob('*.db')))==1
    assert not list(directory.glob('.pending-*'))


def test_job_failure_does_not_skip_backup_and_status_has_no_secrets(db,tmp_path,monkeypatch):
    monkeypatch.setattr('hospital.maintenance.configured',lambda:True)
    def fail(*args): raise RuntimeError('secret-provider-key')
    monkeypatch.setattr('hospital.maintenance.dispatch',fail)
    run_once(db.path,tmp_path/'backups')
    rows={r['job']:r for r in db.records('SELECT * FROM maintenance_status')}
    assert rows['reminders']['state']=='ERROR'
    assert rows['backup']['state']=='OK' and rows['worker']['state']=='OK'
    assert 'secret-provider-key' not in str(rows)


def test_disabled_delivery_does_not_send(db,tmp_path,monkeypatch):
    monkeypatch.setattr('hospital.maintenance.configured',lambda:False)
    def fail(*args): pytest.fail('must not send')
    monkeypatch.setattr('hospital.maintenance.dispatch',fail)
    run_once(db.path)
    rows={r['job']:r for r in db.records('SELECT * FROM maintenance_status')}
    assert rows['reminders']['state']=='DISABLED' and rows['backup']['state']=='DISABLED'


def test_runtime_refuses_missing_mount_and_outside_database(tmp_path,monkeypatch):
    monkeypatch.setenv('MEDAGENT_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('MEDAGENT_V2_DB_PATH',str(tmp_path/'hospital.db'))
    monkeypatch.setenv('MEDAGENT_BACKUP_DIR',str(tmp_path/'backups'))
    monkeypatch.setenv('MEDAGENT_REQUIRE_MOUNT','true')
    monkeypatch.setattr('hospital.runtime.os.path.ismount',lambda _:False)
    with pytest.raises(ValueError,match='not mounted'): configuration()
    monkeypatch.setenv('MEDAGENT_REQUIRE_MOUNT','false')
    assert configuration()[0]==tmp_path/'hospital.db'
    monkeypatch.setenv('MEDAGENT_V2_DB_PATH',str(tmp_path.parent/'outside.db'))
    with pytest.raises(ValueError): configuration()


def test_supervisor_stops_surviving_process_on_child_failure(tmp_path):
    pidfile=tmp_path/'child.pid'
    child="import os,time; from pathlib import Path; Path(%r).write_text(str(os.getpid())); time.sleep(60)" % str(pidfile)
    assert supervise([[sys.executable,'-c',child],[sys.executable,'-c','import time; time.sleep(.5)']])==1
    assert pidfile.exists()
    with pytest.raises(ProcessLookupError): os.kill(int(pidfile.read_text()),0)
