"""Supervise the web app and worker on one host sharing one SQLite file."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading


def configuration():
    path=Path(os.environ['MEDAGENT_V2_DB_PATH']).resolve()
    root=Path(os.environ['MEDAGENT_DATA_DIR']).resolve()
    if not root.is_dir() or not path.is_relative_to(root):
        raise ValueError('Database must be inside the existing data directory.')
    if os.getenv('MEDAGENT_REQUIRE_MOUNT','true')=='true' and not os.path.ismount(root):
        raise ValueError('Persistent data directory is not mounted; refusing ephemeral fallback.')
    backup=Path(os.environ['MEDAGENT_BACKUP_DIR']).resolve()
    if not backup.is_relative_to(root) or backup==root or backup==path:
        raise ValueError('Use a dedicated backup directory inside the data mount.')
    if not path.parent.is_dir():
        raise ValueError('Database parent directory does not exist.')
    port=int(os.getenv('PORT','8501'))
    if not 1<=port<=65535:
        raise ValueError('Invalid port.')
    os.environ['MEDAGENT_V2_DB_PATH']=str(path)
    os.environ['MEDAGENT_BACKUP_DIR']=str(backup)
    os.environ['MEDAGENT_APP_VERSION']='v2'
    return path,port


def supervise(commands,stop=None):
    stop=stop or threading.Event()
    children=[]
    try:
        for command in commands:
            children.append(subprocess.Popen(command))
        while not stop.wait(1):
            if any(child.poll() is not None for child in children):
                # Exit nonzero so the hosting platform restarts the complete pair.
                return 1
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main():
    os.umask(0o077)
    path,port=configuration()
    from .service import Hospital
    from .auth import initialize_auth
    Hospital(str(path))
    initialize_auth(str(path))
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_:stop.set())
    app=str(Path(__file__).resolve().parents[1]/'app_v2.py')
    commands=[
        [sys.executable,'-m','hospital.manage','--database',str(path),'worker'],
        [sys.executable,'-m','streamlit','run',app,'--server.port',str(port),
         '--server.address','0.0.0.0','--server.headless','true'],
    ]
    return supervise(commands,stop)


if __name__=='__main__':
    sys.exit(main())
