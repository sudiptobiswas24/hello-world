"""
A rep in the browser: she writes down an enquiry from the exhibition,
logs the call with a follow-up, converts the lead to a customer and is
taken to its first opportunity, values it and quotes it, landing on the
draft quotation. Each step is read back from the database.
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import Party
from apps.sales.crm import Activity, Lead, Opportunity
from apps.sales.models import Quotation, QuotationLine, QuotationStatus
from apps.sales.tests_base import carries_every_customer

from .tests_browser import BrowserTestCase


class LeadToQuotationInTheBrowserTests(BrowserTestCase):
    def test_enquiry_to_customer_to_quotation(self):
        rep = self.person("Sales Rep")
        carries_every_customer(rep)
        page = self.sign_in(rep, "/app/sales/leads/new")
        page.get_by_label("Who asked").fill("Shree Cement")
        page.get_by_label("Contact").fill("R. Mehta")
        page.get_by_label("City").fill("Pune")
        page.get_by_label("From").select_option(label="Exhibition")
        page.get_by_label("What they asked for").fill("20,000 cement sacks a month")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/sales/leads/\d+$"))
        lead = Lead.objects.get()
        self.assertEqual((lead.company_name, lead.source, lead.owner.code),
                         ("Shree Cement", "exhibition", f"REP-{rep.pk}"))

        page.get_by_role("button", name="Log a call or visit").click()
        form = page.get_by_role("form", name="Log a call or visit")
        form.get_by_label("What was said or done").fill("Wants samples by Friday")
        form.get_by_role("button", name="Log a call or visit").click()
        expect(page.locator("section.related", has_text="Calls and visits").locator("tbody")).to_contain_text("Wants samples")
        self.assertEqual(Activity.objects.get().lead, lead)

        page.get_by_role("button", name="Convert to a customer").click()
        convert = page.get_by_role("form", name="Convert to a customer")
        convert.get_by_label("Customer code").fill("C-SHREE")
        convert.get_by_role("button", name="Convert to a customer").click()
        page.wait_for_url(re.compile(r"/sales/opportunities/\d+$"))
        party = Party.objects.get(code="C-SHREE")
        opportunity = Opportunity.objects.get(customer=party)
        self.assertEqual((opportunity.lead, opportunity.stage), (lead, "new"))
        self.assertEqual(Lead.objects.get().status, "converted")

        page.get_by_label("Worth, before tax").fill("250000")
        page.get_by_role("button", name="Save", exact=True).click()
        self.toast(page, "Saved")
        page.get_by_role("button", name="Quote it").click()
        quote = page.get_by_role("form", name="Quote it")
        # Thirty days on: typed as 31 December 2026, it was past from 1 January 2027 and refused (calc_g58).
        quote.get_by_label("Valid until").fill((timezone.localdate() + datetime.timedelta(days=30)).isoformat())
        quote.get_by_role("button", name="Quote it").click()
        page.wait_for_url(re.compile(r"/sales/quotations/\d+$"))
        quotation = Quotation.objects.get()
        opportunity.refresh_from_db()
        self.assertEqual((quotation.customer, opportunity.stage, opportunity.value, opportunity.quotation),
                         (party, "quoted", Decimal("250000.00"), quotation))
        self.assertEqual(self.problems, [])


class ALapsedQuoteRevisedInTheBrowserTests(BrowserTestCase):
    """
    The customer comes back after the quote lapsed. Accepted, it is refused and marked expired: "Re-quote instead".
    The screen offered Revise only on a sent quote, and on a sent one that had lapsed it was refused: the revision,
    dated today, copied the old valid-until, and that was "valid_until cannot be before the quotation date".
    """

    def test_the_rep_revises_it_for_as_long_as_it_was_offered(self):
        rep = self.person("Sales Rep")
        carries_every_customer(rep)
        today = timezone.localdate()
        quotation = Quotation.objects.create(customer=self.customer, quotation_date=today - datetime.timedelta(days=60),
                                             valid_until=today - datetime.timedelta(days=30), currency=self.usd)
        QuotationLine.objects.create(quotation=quotation, item=self.item, uom=self.uom, quantity=Decimal("5"),
                                     unit_price=Decimal("90"), revenue_account=self.revenue)
        quotation.mark_sent()
        with self.assertRaisesMessage(ValidationError, "Re-quote instead"):
            quotation.accept()

        page = self.sign_in(rep, f"/app/sales/quotations/{quotation.pk}")
        expect(page.locator(".pill", has_text="expired")).to_be_visible()
        page.get_by_role("button", name="Revise").click()
        page.wait_for_url(re.compile(rf"/sales/quotations/(?!{quotation.pk}$)\d+$"))
        revision = Quotation.objects.get(revision_of=quotation)
        expect(page.get_by_label("Valid until")).to_have_value((today + datetime.timedelta(days=30)).isoformat())
        quotation.refresh_from_db()
        self.assertEqual((quotation.status, revision.status, revision.quotation_date, revision.valid_until),
                         (QuotationStatus.SUPERSEDED, QuotationStatus.DRAFT, today,
                          today + datetime.timedelta(days=30)))
        self.assertEqual(self.problems, [])
