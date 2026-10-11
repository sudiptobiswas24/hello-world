"""
Features only the Django admin reached, driven in the browser by the
people who use them: the controller writes a debt off and recovers it, a
purchasing clerk makes a vendor a customer too, and the process engineer
laminates a sack by giving it a coating blend. Each step is read back
from the database.
"""

import contextlib
import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.manufacturing.tests_coating import CoatingTestCase
from apps.manufacturing.woven import BagSpecification
from apps.sales.models import InvoiceWriteOff

from .tests_browser import BrowserMixin, BrowserTestCase


def panel(page, title):
    # A wait for what an add put in a panel looks in its rows: the add form
    # still open beside them shows the choice made, and waiting on that
    # read the database before the server had saved.
    return page.locator("section.related").filter(has=page.get_by_role("heading", name=title, exact=True))


@contextlib.contextmanager
def answering_each(case, page, *replies):
    """The questions the next step asks, in turn; one more or one fewer is a fault."""
    asked = []
    queue = list(replies)

    def answer(dialog):
        asked.append(dialog.message)
        dialog.accept(queue.pop(0)) if queue else dialog.dismiss()

    page.remove_listener("dialog", page._unexpected_dialog)
    page.on("dialog", answer)
    try:
        yield asked
    finally:
        page.remove_listener("dialog", answer)
        page.on("dialog", page._unexpected_dialog)
    case.assertEqual(len(asked), len(replies), asked)


class WriteOffInTheBrowserTests(BrowserTestCase):
    def test_written_off_in_part_and_recovered(self):
        invoice = self.bill(self.make_order("10", "100"))
        page = self.sign_in(self.person("Controller"), f"/app/sales/invoices/{invoice.pk}")
        with answering_each(self, page, "600", "Settled at 40 paise"):
            page.get_by_role("button", name="Write off").click()
            expect(page.locator(".toast, [role=status]").first).to_contain_text("Written off")
        write_off = InvoiceWriteOff.objects.get()
        self.assertEqual((write_off.amount, write_off.reason), (Decimal("600.00"), "Settled at 40 paise"))

        page.goto(f"{self.live_server_url}/app/accounts/bad-debts")
        expect(page.locator("tbody")).to_contain_text("Settled at 40 paise")
        page.locator("tbody tr").first.click()
        page.wait_for_url(re.compile(r"/accounts/bad-debts/\d+$"))
        page.get_by_role("button", name="Recover").click()
        page.get_by_role("form", name="Recover").get_by_role("button", name="Recover").click()
        expect(page.locator("main")).to_contain_text("Recovered")
        write_off.refresh_from_db()
        self.assertTrue(write_off.is_recovered())
        self.assertEqual(self.balance(self.bad_debt), Decimal("0"))
        self.assertEqual(self.problems, [])


class PartyRoleInTheBrowserTests(BrowserTestCase):
    def test_a_vendor_made_a_customer_too(self):
        vendor = Party.objects.create(code="V-9", name="Granule House", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        page = self.sign_in(self.person("Purchasing Clerk"), f"/app/purchasing/vendors/{vendor.pk}")
        expect(panel(page, "Roles")).to_contain_text("Vendor")
        page.get_by_role("button", name="Add a role").click()
        form = page.get_by_role("form", name="Add a role")
        form.get_by_label("Role").select_option(label="Customer")
        form.get_by_role("button", name="Add a role").click()
        expect(panel(page, "Roles").locator("tbody")).to_contain_text("Customer")
        self.assertEqual(set(vendor.role_assignments.values_list("role", flat=True)), {"vendor", "customer"})
        expect(page.get_by_role("button", name="Add a role")).to_have_count(0)
        self.assertEqual(self.problems, [])


class CoatingInTheBrowserTests(BrowserMixin, CoatingTestCase, StaticLiveServerTestCase):
    def test_laminated_by_its_blend(self):
        sack = self.bag()
        page = self.sign_in(self.login_for("Process Engineer"), f"/app/making/bags/{sack.pk}")
        page.get_by_role("button", name="Add a polymer").click()
        form = page.get_by_role("form", name="Add a polymer")
        form.get_by_role("combobox", name="Polymer").fill("LAM-PP")
        page.get_by_role("option", name=re.compile("LAM-PP")).click()
        form.get_by_label("Parts").fill("80")
        form.get_by_label("Coating GSM").fill("15")
        form.get_by_role("button", name="Add a polymer").click()
        expect(panel(page, "Coating blend").locator("tbody")).to_contain_text("LAM-PP")
        sack.refresh_from_db()
        self.assertEqual((sack.is_laminated, sack.lamination_gsm), (True, Decimal("15.00")))

        panel(page, "Coating blend").get_by_role("button", name="Remove the line").click()
        expect(panel(page, "Coating blend")).to_contain_text("None yet")
        sack = BagSpecification.objects.get(pk=sack.pk)
        self.assertEqual((sack.is_laminated, sack.coating_lines.count()), (False, 0))
        self.assertEqual(self.problems, [])
