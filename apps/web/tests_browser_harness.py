"""
A browser test ends while the server may still be answering what its
page asked: a search the page gave up on when it moved on. The flush
that follows waited for the page to go quiet, which it already was, and
on PostgreSQL the request's SELECT and the flush's TRUNCATE deadlocked;
the next test's setUp then failed on what the flush had left. A gate on
a loaded machine lost two tests that way. The flush now waits for the
server's own count of requests begun and not finished.
"""

import threading
import time
from unittest import mock

from django.test import SimpleTestCase

from apps.core.views import CurrencyViewSet

from .tests_browser import BrowserTestCase, InFlight


class TheFlushWaitsForTheServerTests(BrowserTestCase):
    def test_a_request_the_browser_gave_up_on_is_answered_before_the_flush(self):
        asked, answer = threading.Event(), threading.Event()
        listing = CurrencyViewSet.list

        def slowly(view, request, *args, **kwargs):
            asked.set()
            answer.wait(10)
            return listing(view, request, *args, **kwargs)

        page = self.sign_in(self.person("Bookkeeper"), "/app/")
        page.wait_for_load_state("networkidle")
        with mock.patch.object(CurrencyViewSet, "list", slowly):
            page.evaluate("() => { fetch('/api/core/currencies/'); }")
            self.assertTrue(asked.wait(10))
            self.context.close()  # the browser gives up; the server is still answering
            self.assertGreaterEqual(self.in_flight.count, 1)
            threading.Timer(0.3, answer.set).start()
            started = time.monotonic()
            self.in_flight.wait()
            self.assertGreaterEqual(time.monotonic() - started, 0.2)
        self.assertEqual(self.in_flight.count, 0)


class InFlightTests(SimpleTestCase):
    def test_a_request_that_never_ends_is_reported_not_waited_out(self):
        in_flight = InFlight()
        in_flight.waits_for = 0.1
        in_flight.started()
        with self.assertRaisesMessage(AssertionError, "still answering 1 request(s)"):
            in_flight.wait()
        in_flight.finished()
        in_flight.wait()
