"""
The Letter of Undertaking and what setup seeded, kept from the office as the controller does it (O239).
"""

import datetime
import re

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.gst import GstSettings, LetterOfUndertaking
from apps.accounting.models import FiscalPosition
from apps.accounting.tcs import SeededDefault
from apps.core.models import Country
from apps.gst.tests_einvoice import EInvoiceTestCase

from .tests_browser import BrowserMixin, BrowserTestCase


class LutAndSeededDefaultsInTheBrowserTests(BrowserTestCase):
    def test_the_lut_is_kept_and_what_was_seeded_is_reviewed(self):
        interstate = FiscalPosition.objects.create(code="INTER", name="Inter-state")
        GstSettings.objects.create(gstin="27AABCD1234E1Z8", interstate_position=interstate)
        controller = self.person("Controller")
        page = self.sign_in(controller, "/app/settings/luts/new")
        page.get_by_label("ARN").fill("AD190426000123X")
        page.get_by_label("Financial year").fill("2026-27")
        page.get_by_label("Filed on").fill("2026-03-25")
        page.get_by_label("Valid from").fill("2026-04-01")
        page.get_by_label("Valid to").fill("2027-03-31")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/settings/luts/\d+$"))
        lut = LetterOfUndertaking.objects.get()
        self.assertEqual((lut.arn, lut.valid_to), ("AD190426000123X", datetime.date(2027, 3, 31)))

        seeded = SeededDefault.objects.create(model="accounting.account", object_id=self.bank.pk, label="1010 Bank")
        page.goto(f"{self.live_server_url}/app/settings/seeded-defaults")
        expect(page.locator("main")).to_contain_text("To review")
        page.goto(f"{self.live_server_url}/app/settings/seeded-defaults/{seeded.pk}")
        page.get_by_role("button", name="Mark reviewed").click()
        # "Reviewed" alone was met at once by the field labels "Reviewed by" and "Reviewed on", before the review
        # landed: the database read below was then decided by speed. The button shows only while it is not
        # reviewed, and it was there to be pressed, so its going is the review saved.
        expect(page.get_by_role("button", name="Mark reviewed")).to_have_count(0)
        seeded.refresh_from_db()
        self.assertEqual((seeded.reviewed_by, seeded.reviewed_on is not None), (controller, True))
        self.assertEqual(self.problems, [])


class ChargedTaxesInTheBrowserTests(BrowserMixin, EInvoiceTestCase, StaticLiveServerTestCase):
    """
    O610: a line shows the taxes it is charged. An export under the LUT keeps the item's CGST9 and SGST9 as chosen
    and is charged IGST0: posted, the line said CGST9, SGST9 beside a tax of 0.00, and its print said IGST0.
    """

    def export(self, post):
        abroad = self.party("EXPORT", currency=self.usd, gst_registration="overseas", fiscal_position=self.lut)
        uae = Country.objects.get_or_create(code="AE", defaults={"name": "United Arab Emirates"})[0]
        self.address(abroad, "Jebel Ali Free Zone", "Dubai", "", "", country=uae)
        # The GST fixture's invoice, with its lines' taxes: BrowserMixin.invoice comes first and takes neither.
        return EInvoiceTestCase.invoice(self, abroad, [(self.sack, "1000", "1.37", "0", self.pair)],
                                        currency=self.usd, post=post)

    def test_a_posted_export_shows_what_it_is_charged(self):
        invoice = self.export(post=True)
        page = self.sign_in(self.login_for("AR Manager"), f"/app/sales/invoices/{invoice.pk}")
        line = page.get_by_role("row", name=re.compile("Woven sack"))
        expect(line).to_contain_text("IGST0")
        expect(line).not_to_contain_text("CGST9")
        self.assertEqual(self.problems, [])

    def test_a_draft_export_says_what_its_choice_is_charged(self):
        invoice = self.export(post=False)
        page = self.sign_in(self.login_for("AR Manager"), f"/app/sales/invoices/{invoice.pk}")
        line = page.get_by_role("row", name=re.compile("Woven sack"))
        # The choice stays the item's, to change; beside it, what that choice is charged here.
        expect(line).to_contain_text("CGST9, SGST9")
        expect(line).to_contain_text("Charged IGST0")
        self.assertEqual(self.problems, [])
