"""
A rep in the browser: she writes down an enquiry from the exhibition,
logs the call with a follow-up, converts the lead to a customer and is
taken to its first opportunity, values it and quotes it, landing on the
draft quotation. Each step is read back from the database.
"""

import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.models import Party
from apps.sales.crm import Activity, Lead, Opportunity
from apps.sales.models import Quotation
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
        quote.get_by_label("Valid until").fill("2026-12-31")
        quote.get_by_role("button", name="Quote it").click()
        page.wait_for_url(re.compile(r"/sales/quotations/\d+$"))
        quotation = Quotation.objects.get()
        opportunity.refresh_from_db()
        self.assertEqual((quotation.customer, opportunity.stage, opportunity.value, opportunity.quotation),
                         (party, "quoted", Decimal("250000.00"), quotation))
        self.assertEqual(self.problems, [])
