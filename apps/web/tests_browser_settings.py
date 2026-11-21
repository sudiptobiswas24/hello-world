"""
The party page and the settings in the browser, as the people who keep
them: a rep adds a delivery address and a contact to their customer and
cannot set its GST registration; the controller adds a unit of measure,
and the unit is offered at once where units are chosen, without a reload.
Each step is read back from the database.
"""

import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.models import Address, Contact, UnitOfMeasure

from .tests_browser import BrowserTestCase


class PartyPageInTheBrowserTests(BrowserTestCase):
    def test_the_rep_adds_an_address_and_a_contact(self):
        page = self.sign_in(self.person("Sales Rep"), f"/app/sales/customers/{self.customer.pk}")
        page.get_by_role("button", name="Add an address").click()
        form = page.get_by_role("form", name="Add an address")
        form.get_by_label("For", exact=True).select_option(label="Shipping")
        form.get_by_label("Label").fill("Godown 2")
        form.get_by_label("Line 1").fill("Plot 9, Wadi")
        form.get_by_label("City").fill("Nagpur")
        form.get_by_role("button", name="Add an address").click()
        expect(page.locator("section.related", has_text="Addresses")).to_contain_text("Plot 9, Wadi, Nagpur")
        address = Address.objects.get(party=self.customer)
        self.assertEqual((address.address_type, address.label, address.city), ("shipping", "Godown 2", "Nagpur"))

        page.get_by_role("button", name="Add a contact").click()
        form = page.get_by_role("form", name="Add a contact")
        form.get_by_label("First name").fill("Meera")
        form.get_by_label("Title").fill("Purchase manager")
        form.get_by_role("button", name="Add a contact").click()
        expect(page.locator("section.related", has_text="Contacts")).to_contain_text("Meera")
        self.assertEqual(Contact.objects.get(party=self.customer).job_title, "Purchase manager")

        # GST reads the registration the GST officer keeps: the rep sees it and adds none.
        expect(page.locator("section.related", has_text="GST")).to_be_visible()
        expect(page.get_by_role("button", name="Add the GST registration")).to_have_count(0)
        self.assertEqual(self.problems, [])


class SettingsInTheBrowserTests(BrowserTestCase):
    def test_the_controller_adds_a_unit_and_it_is_offered_at_once(self):
        page = self.sign_in(self.person("Controller"), "/app/settings/units/new")
        page.get_by_label("Code").fill("bale")
        page.get_by_label("Name").fill("Bale")
        page.get_by_role("button", name="Create").click()
        page.wait_for_url(re.compile(r"/settings/units/\d+$"))
        self.assertTrue(UnitOfMeasure.objects.filter(code="bale").exists())

        # A short master is read once and kept: the write is what refreshes it.
        page.goto(f"{self.live_server_url}/app/settings/units/new")
        expect(page.get_by_label("Base unit").locator("option", has_text="bale")).to_have_count(1)
        self.assertEqual(self.problems, [])
