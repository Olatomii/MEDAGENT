"""One scheduled reminder pass; credentials are read only from the environment."""
import os
from .notifications import configured, dispatch


def main():
    database = os.getenv('MEDAGENT_DATABASE_URL', '')
    if not configured() or not database.startswith(('postgresql://', 'postgres://')):
        raise SystemExit('Reminder job requires an enabled provider and a PostgreSQL database secret.')
    try:
        count = dispatch(database)
    except Exception:
        # Connection exceptions can contain credentials; never print raw tracebacks.
        raise SystemExit('Reminder check failed. Check database connectivity and configuration.') from None
    print(f'Provider accepted {count} reminders. Review notification statuses for delivery failures.')


if __name__ == '__main__':
    main()
