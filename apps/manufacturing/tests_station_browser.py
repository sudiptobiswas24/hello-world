"""
The station screen driven in a real browser: sign in with a PIN, scan a
loom, take a reading off the scale, confirm, and print the label.

Skipped where Playwright or a Chromium it can launch is missing; they
are not a dependency of the application, only of this check. Set
PLAYWRIGHT_CHROMIUM_EXECUTABLE to use a Chromium already on the machine.
"""

import os
import unittest
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.test import LiveServerTestCase

from .rolls import FabricRoll
from .tests_station import StationTestCase

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class StationScreenTests(StationTestCase, LiveServerTestCase):
    @classmethod
    def _databases_support_transactions(cls):
        # The fixture comes from a TestCase, which wraps each test in a
        # transaction; the live server's thread shares the in-memory
        # database and cannot work inside one. Saying transactions are
        # unsupported makes TestCase flush between tests instead.
        return False

    @classmethod
    def setUpClass(cls):
        # Playwright's sync API runs an event loop on this thread, which
        # Django otherwise takes for async code touching the database.
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        # PLAYWRIGHT_CHROMIUM_EXECUTABLE points at a Chromium when the one
        # Playwright downloads for itself is not there.
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
        try:
            cls.browser = cls.playwright.chromium.launch(executable_path=executable)
        except Exception as error:  # noqa: BLE001 - any launch failure means no browser
            cls.playwright.stop()
            super().tearDownClass()
            raise unittest.SkipTest(f"No usable Chromium: {str(error).splitlines()[0]}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)

    def setUp(self):
        super().setUp()
        device = User.objects.create_user("station-lx1", password="loom-exit-1")
        device.user_permissions.add(Permission.objects.get(codename="weigh_at_station"))
        self.pin = self.operator.issue_pin()
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 800})
        self.addCleanup(self.page.close)

    def open_station(self):
        page = self.page
        page.goto(f"{self.live_server_url}/station/{self.station.code}/")
        page.fill("#id_username", "station-lx1")
        page.fill("#id_password", "loom-exit-1")
        page.click("button[type=submit]")
        page.wait_for_selector("#keys button")
        return page

    def sign_in(self, page):
        for digit in self.pin:
            page.click(f"#keys button:text-is('{digit}')")
        page.wait_for_selector("#weigh.on")

    def test_a_roll_from_scale_to_label(self):
        page = self.open_station()
        self.sign_in(page)
        self.assertIn("EMP-0142", page.inner_text("#hdr-operator"))

        page.fill("#loom", "L-17")
        page.press("#loom", "Enter")
        page.wait_for_function("document.getElementById('contractor').textContent.length > 0")
        self.assertEqual(page.inner_text("#contractor"), "Contractor A")

        page.evaluate("stationScale.feed('ST,GS,+  106.80 kg')")
        self.assertEqual(page.inner_text("#net"), "104.40")
        self.assertEqual(page.inner_text("#derived"), "1000 m")

        page.fill("#declared", "1006")
        self.assertEqual(page.inner_text("#variance"), "+0.6%")
        self.assertEqual(page.inner_text("#btn-confirm"), "Confirm and print label")

        # Wrapped: evaluate() calls an expression that is itself a function.
        page.evaluate("() => { window.print = () => { window.__printed = "
                      "document.getElementById('print-area').innerHTML; }; }")
        page.click("#btn-confirm")
        page.wait_for_function("window.__printed !== undefined")

        roll = FabricRoll.objects.get()
        self.assertEqual(roll.weighed_by, self.operator)
        self.assertEqual(roll.net_weight_kg, Decimal("104.4"))
        printed = page.evaluate("window.__printed")
        self.assertIn(roll.lot.code, printed)
        self.assertIn("<svg", printed)
        self.assertIn(roll.lot.code, page.inner_text("#flash"))

    def test_an_unsettled_reading_cannot_be_confirmed(self):
        page = self.open_station()
        self.sign_in(page)
        page.fill("#loom", "L-17")
        page.press("#loom", "Enter")
        page.wait_for_function("document.getElementById('contractor').textContent.length > 0")
        page.evaluate("stationScale.feed('US,GS,+  106.80 kg')")
        page.fill("#declared", "1006")
        self.assertTrue(page.is_disabled("#btn-confirm"))

    def test_over_tolerance_says_so_before_it_is_confirmed(self):
        page = self.open_station()
        self.sign_in(page)
        page.fill("#loom", "L-17")
        page.press("#loom", "Enter")
        page.wait_for_function("document.getElementById('contractor').textContent.length > 0")
        page.evaluate("stationScale.feed('ST,GS,+  106.80 kg')")
        page.fill("#declared", "1062")
        self.assertEqual(page.inner_text("#variance"), "+6.2%")
        self.assertEqual(page.inner_text("#btn-confirm"), "Confirm with exception and print")

    def test_a_wrong_pin_is_refused_on_the_keypad(self):
        page = self.open_station()
        wrong = "111112" if self.pin != "111112" else "111113"
        for digit in wrong:
            page.click(f"#keys button:text-is('{digit}')")
        page.wait_for_function("document.getElementById('pin-error').textContent.length > 0")
        self.assertIn("not recognised", page.inner_text("#pin-error"))
        self.assertFalse(page.is_visible("#weigh"))

    def test_the_screen_speaks_bengali(self):
        page = self.open_station()
        page.click("#langs button:text-is('বাংলা')")
        self.assertEqual(page.inner_text("#signin h2"), "আপনার পিন দিন")
