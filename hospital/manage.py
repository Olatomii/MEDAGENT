"""Maintenance commands; never reads production configuration implicitly."""
import argparse
from .service import Hospital
from .agents import process_events
from .import_legacy import import_snapshot


def main():
    parser=argparse.ArgumentParser(description='MedAgent development database tools')
    parser.add_argument('--database',required=True,help='Destination v2 database path')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('dispatch',help='Process pending durable events once')
    importer=sub.add_parser('import-legacy',help='Import a separate, immutable legacy snapshot read-only')
    importer.add_argument('source')
    args=parser.parse_args()
    hospital=Hospital(args.database)
    if args.command=='import-legacy':
        print(f'Imported {import_snapshot(hospital,args.source)} records.')
    print(f'Processed {process_events(hospital.path)} events.')


if __name__=='__main__':
    main()
