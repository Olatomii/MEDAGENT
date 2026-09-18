"""Maintenance commands; never reads production configuration implicitly."""
import argparse
import time
from .service import Hospital
from .agents import process_events
from .import_legacy import import_snapshot
from .notifications import dispatch


def main():
    parser=argparse.ArgumentParser(description='MedAgent development database tools')
    parser.add_argument('--database',required=True,help='Destination v2 database path')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('dispatch',help='Process pending durable events once')
    sub.add_parser('reminders',help='Send due reminders once, only when v2 delivery is enabled')
    worker=sub.add_parser('worker',help='Run events and reminders continuously against the same database')
    worker.add_argument('--interval',type=int,default=60)
    importer=sub.add_parser('import-legacy',help='Import a separate, immutable legacy snapshot read-only')
    importer.add_argument('source')
    args=parser.parse_args()
    hospital=Hospital(args.database)
    if args.command=='worker':
        if args.interval<10:
            parser.error('Worker interval must be at least 10 seconds.')
        try:
            while True:
                process_events(hospital.path)
                dispatch(hospital.path)
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
