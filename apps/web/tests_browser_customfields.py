"""
The Controller adds "Credit rating" to customers from Settings; the rep
opens a customer, finds the box beside the customer's own, picks a
rating and saves; it is there when the page opens again.
"""

import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.customfields import CustomField
from apps.core.models import Party

from .tests_browser import BrowserTestCase


class CustomFieldsInTheBrowserTests(BrowserTestCase):
    def test_defined_in_settings_and_filled_on_the_customer(self):
        page = self.sign_in(self.person("Controller"), "/app/settings/custom-fields/new")
        page.get_by_label("On", exact=True).select_option("core.party")
        page.get_by_label("Field", exact=True).fill("Credit rating")
        page.get_by_label("Key").fill("credit_rating")
        page.get_by_label("Holds").select_option("choice")
        page.get_by_label("The choices").fill("A\nB\nC")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/settings/custom-fields/\d+$"))
        self.assertEqual(CustomField.objects.get(key="credit_rating").choice_list(), ["A", "B", "C"])

        rep = self.sign_in(self.person("Sales Rep"), f"/app/sales/customers/{self.customer.pk}", page=self.new_page())
        rep.get_by_label("Credit rating").select_option("B")
        rep.get_by_role("button", name="Save", exact=True).click()
        self.toast(rep, "Saved")
        self.assertEqual(Party.objects.get(pk=self.customer.pk).extra, {"credit_rating": "B"})
        rep.reload()
        expect(rep.get_by_label("Credit rating")).to_have_value("B")
        self.assertEqual(self.problems, [])
