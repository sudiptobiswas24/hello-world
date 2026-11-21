"""
The Controller opens Health and reads that the books agree; opens the
problem a clerk hit, under the reference the clerk was shown, and marks
it dealt with, saying what was done.
"""

import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.core.cache import cache

from apps.core.errors import ServerError

from .tests_browser import BrowserTestCase


class HealthInTheBrowserTests(BrowserTestCase):
    def test_the_books_agree_and_a_problem_is_dealt_with(self):
        cache.clear()
        ServerError.objects.create(ref="E-7K3QM", path="/api/sales/invoices/7/post_invoice/", method="POST",
                                   kind="ZeroDivisionError", message="division by zero", traceback="Traceback ...")
        page = self.sign_in(self.person("Controller"), "/app/settings/health")
        books = page.locator("section.report").first
        expect(books).to_contain_text("Trial balance")
        expect(page.locator("tr", has_text="Receivables agree with the invoices")).to_contain_text("Agrees")
        expect(page.locator("tr", has_text="Closed months stayed closed")).to_contain_text("Agrees")
        expect(page.locator("main")).to_contain_text("1 not yet dealt with")

        page.get_by_role("link", name="1 not yet dealt with").click()
        page.wait_for_url(re.compile(r"/settings/problems"))
        self.rows().first.click()
        page.wait_for_url(re.compile(r"/settings/problems/\d+$"))
        expect(page.locator("main")).to_contain_text("E-7K3QM")
        page.get_by_role("button", name="Dealt with").click()
        form = page.get_by_role("form", name="Dealt with")
        form.get_by_label("What was done").fill("The invoice had no lines; refused in words now.")
        form.get_by_role("button", name="Dealt with").click()
        self.toast(page, "Marked dealt with")
        row = ServerError.objects.get(ref="E-7K3QM")
        self.assertEqual((row.note, row.resolved_by.username), ("The invoice had no lines; refused in words now.", "controller"))
        self.assertEqual(self.problems, [])
