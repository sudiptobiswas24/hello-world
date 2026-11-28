"""
A browser test's body and its live server sit on two SQLite connections
over one shared cache. A table one of them is still reading is locked
against the other's write, and SQLite says so at once ("database table
is locked") rather than waiting as it does for a busy file: no busy
timeout covers it. The test creating its second person while the first
one's page still had a request in flight lost a run that way. The cursor
every connection gets in a browser test waits for the read to end.
"""

import sqlite3
import threading
import time

from django.test import SimpleTestCase

from .tests_browser import OneCallAtATime


class WriteWaitsForReadTests(SimpleTestCase):
    def test_a_write_into_a_table_another_connection_is_reading_waits_for_that_read_to_end(self):
        uri = "file:write_waits_for_read?mode=memory&cache=shared"
        reader = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
        writer = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        reader.execute("CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT)")
        reader.execute("INSERT INTO person (name) VALUES ('supervisor')")
        # A request in flight: a read transaction open on the table.
        reader.execute("BEGIN")
        reader.execute("SELECT name FROM person").fetchall()
        with self.assertRaisesMessage(sqlite3.OperationalError, "database table is locked"):
            writer.execute("INSERT INTO person (name) VALUES ('nobody')")

        request_ends = threading.Timer(0.3, lambda: reader.execute("COMMIT"))
        request_ends.start()
        self.addCleanup(request_ends.join)
        started = time.monotonic()
        writer.cursor(factory=OneCallAtATime).execute("INSERT INTO person (name) VALUES ('fitter')")
        self.assertGreaterEqual(time.monotonic() - started, 0.2)
        self.assertEqual(writer.execute("SELECT count(*) FROM person").fetchone()[0], 2)

    def test_any_other_refusal_is_raised_at_once(self):
        connection = sqlite3.connect("file:refusal_at_once?mode=memory&cache=shared", uri=True,
                                     check_same_thread=False, isolation_level=None)
        self.addCleanup(connection.close)
        started = time.monotonic()
        with self.assertRaisesMessage(sqlite3.OperationalError, "no such table"):
            connection.cursor(factory=OneCallAtATime).execute("INSERT INTO nowhere (name) VALUES ('x')")
        self.assertLess(time.monotonic() - started, 1)
