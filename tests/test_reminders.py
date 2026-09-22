import datetime as dt
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import reminders


class RemindersTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = self.directory.name + '/reminders.db'
        self.now = dt.datetime(2026, 9, 17, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=1)))
        with sqlite3.connect(self.path) as c:
            c.executescript('''CREATE TABLE doctors(doc_id INTEGER PRIMARY KEY,specialty TEXT);
            INSERT INTO doctors VALUES(1,'General Practice');
            CREATE TABLE appointments(queue_number TEXT, booking_date TEXT, status TEXT,
                location TEXT,triage_level INTEGER,payment_status TEXT,doc_id INTEGER);
            CREATE TABLE waitlist(patient_name TEXT);
            CREATE TABLE audit_events(message TEXT,is_critical INTEGER);
            INSERT INTO appointments VALUES('Q-test','2026-09-18','WAITING','Nurses Station',4,'Cleared',1);''')
            reminders.migrate(c)
            c.execute("UPDATE appointments SET email='patient@example.com',reminder_opt_in=1,reminder_date='2026-09-18'")

    def send(self, *args):
        return 'provider-test-id'

    def test_repeat_and_restart_do_not_send_twice(self):
        self.assertEqual(reminders.dispatch(self.path, self.now, self.send), 1)
        self.assertEqual(reminders.dispatch(self.path, self.now, self.send), 0)

    def test_simultaneous_dispatch_claims_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            counts = list(pool.map(lambda _: reminders.dispatch(self.path, self.now, self.send), range(2)))
        self.assertEqual(sum(counts), 1)

    def test_disabled_sender_never_contacts_provider(self):
        with patch.dict(os.environ, {'REMINDERS_ENABLED':'false'}), patch('reminders.send_email') as sender:
            self.assertEqual(reminders.dispatch(self.path, self.now), 0)
            sender.assert_not_called()

    def test_no_early_or_late_reminder(self):
        for now in (self.now.replace(hour=8), self.now + dt.timedelta(days=1), self.now - dt.timedelta(days=1)):
            self.assertEqual(reminders.dispatch(self.path, now, self.send), 0)

    def test_ineligible_visits_not_sent(self):
        for column, value in [('status','COMPLETED'),('status','ABSENT'),('status','ADMITTED'),
                              ('status','DIVERTED'),('reminder_opt_in',0),('triage_level',2),
                              ('booking_date','2026-09-19'),('email','bad-address'),('location','Doctor Wait')]:
            with sqlite3.connect(self.path) as c:
                old = c.execute(f'SELECT {column} FROM appointments').fetchone()[0]
                c.execute(f'UPDATE appointments SET {column}=?', (value,))
            self.assertEqual(reminders.dispatch(self.path, self.now, self.send), 0, column)
            with sqlite3.connect(self.path) as c:
                c.execute(f'UPDATE appointments SET {column}=?', (old,))

    def test_timeout_is_visible_and_not_blindly_retried(self):
        def timeout(*args):
            raise TimeoutError('could have reached provider')
        self.assertEqual(reminders.dispatch(self.path, self.now, timeout), 0)
        self.assertEqual(reminders.dispatch(self.path, self.now, self.send), 0)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute('SELECT status FROM email_reminders').fetchone()[0], 'REVIEW_REQUIRED')

    def test_migration_preserves_existing_booking_and_is_repeatable(self):
        with sqlite3.connect(self.path) as c:
            reminders.migrate(c)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM appointments').fetchone()[0], 1)

    def test_address_validation_and_date_only_copy(self):
        for value in ('a@b.com\r\nBcc:x@y.com', 'not an email', 'a@b.com,c@d.com'):
            self.assertFalse(reminders.valid_email(value))
        self.assertTrue(reminders.valid_email('patient+appointment@example.com'))
        self.assertIn('2026-09-18', reminders.message('2026-09-18','General Practice'))
        self.assertNotIn('09:00', reminders.message('2026-09-18','General Practice'))


if __name__ == '__main__':
    unittest.main()
