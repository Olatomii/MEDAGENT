"""Maintenance commands; never reads production configuration implicitly."""
import argparse
import time
import os
import getpass
import json
from .service import Hospital
from .agents import process_events
from .import_legacy import import_snapshot
from .notifications import dispatch
from .auth import initialize_auth,create_staff,disable_staff,reset_password,ROLES


def main():
    parser=argparse.ArgumentParser(description='MedAgent development database tools')
    parser.add_argument('--database',required=True,help='Destination v2 database path')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('dispatch',help='Process pending durable events once')
    staff=sub.add_parser('create-staff',help='Provision staff using a hidden password prompt')
    staff.add_argument('username')
    staff.add_argument('--role',choices=ROLES,required=True)
    disable=sub.add_parser('disable-staff',help='Disable a staff account and revoke its sessions')
    disable.add_argument('username')
    reset=sub.add_parser('reset-password',help='Reset a staff password and revoke all sessions')
    reset.add_argument('username')
    sub.add_parser('reminders',help='Send due reminders once, only when v2 delivery is enabled')
    worker=sub.add_parser('worker',help='Run events and reminders continuously against the same database')
    worker.add_argument('--interval',type=int,default=60)
    importer=sub.add_parser('import-legacy',help='Import a separate, immutable legacy snapshot read-only')
    importer.add_argument('source')
    backup=sub.add_parser('backup',help='Write a private, consistent backup to a new file')
    backup.add_argument('destination')
    restore=sub.add_parser('restore',help='Restore a backup into a new database; never overwrite')
    restore.add_argument('source')
    evaluation=sub.add_parser('evaluate',help='Compare synthetic queue policies; no live records')
    evaluation.add_argument('--seed',type=int,default=42)
    evaluation.add_argument('--count',type=int,default=120)
    evaluation.add_argument('--doctors',type=int,default=2)
    args=parser.parse_args()
    if args.command in ('backup','restore'):
        from .backup import snapshot,restore
        result=(snapshot(args.database,args.destination) if args.command=='backup'
                else restore(args.source,args.database))
        print(f'Wrote {result}. Keep this file private; it contains patient records and password hashes.')
        return
    if args.command=='evaluate':
        from .evaluation import compare
        if args.count<1 or args.doctors<1:
            parser.error('Count and doctors must be positive.')
        print(json.dumps(compare(args.seed,args.count,args.doctors),indent=2))
        return
    hospital=Hospital(args.database)
    initialize_auth(hospital.path)
    if args.command in ('create-staff','reset-password'):
        password=getpass.getpass('Password (12–256 characters): ')
        if password!=getpass.getpass('Confirm password: '):
            parser.error('Passwords do not match.')
        if args.command=='create-staff':
            create_staff(hospital.path,args.username,password,args.role)
            print('Staff account created.')
        else:
            reset_password(hospital.path,args.username,password)
            print('Password changed; all previous sessions revoked.')
        return
    if args.command=='disable-staff':
        disable_staff(hospital.path,args.username)
        print('Staff account disabled; sessions revoked.')
        return
    if args.command=='worker':
        if args.interval<10:
            parser.error('Worker interval must be at least 10 seconds.')
        try:
            while True:
                from .maintenance import run_once
                run_once(hospital.path,os.getenv("MEDAGENT_BACKUP_DIR"))
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return
    if args.command=='reminders':
        print(f'Provider accepted {dispatch(hospital.path)} reminders.')
    if args.command=='import-legacy':
        print(f'Imported {import_snapshot(hospital,args.source)} records.')
    print(f'Processed {process_events(hospital.path)} events.')


if __name__=='__main__':
    main()
