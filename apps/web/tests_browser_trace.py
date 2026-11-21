"""
A batch traced on its own page, as a complaint and a recall need it:
tape batch TAPE-A was made from polymer PP-2609, and 600 kg of it went
to the customer. Both directions on the batch's page, as text.
"""

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.manufacturing.tests_trace import TraceTestCase

from .tests_browser import BrowserMixin


class TraceInTheBrowserTests(BrowserMixin, TraceTestCase, StaticLiveServerTestCase):
    def test_a_batch_says_what_it_came_from_and_who_holds_it(self):
        self.a_run()
        self.ship("600")
        page = self.sign_in(self.person("Quality Manager"), f"/app/stores/batches/{self.tape_lot.pk}")
        made_from = page.locator("section.related", has_text="Made from")
        expect(made_from).to_contain_text("PP-2609")
        page.goto(f"{self.live_server_url}/app/stores/batches/{self.polymer_lot.pk}")
        held = page.locator("section.related", has_text="Who holds it")
        expect(held).to_contain_text(self.customer.name)
        expect(held).to_contain_text("600")
        self.assertEqual(self.problems, [])
