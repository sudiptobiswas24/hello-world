"""
Dana reads the pipeline board and moves the cement sacks to qualified;
opens the lead and reads how warm it is.
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.sales.crm import Lead, Opportunity

from .tests_browser import BrowserTestCase


class CrmBoardInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.manager = self.person("AR Manager")
        self.opportunity = Opportunity.objects.create(customer=self.customer, title="20,000 sacks a month",
                                                      value=Decimal("1200000"), expected_on=datetime.date(2026, 7, 1))
        self.lead = Lead.objects.create(company_name="Shree Cement", contact_name="R. Mehta", phone="98200 11111",
                                        source="exhibition", interest="20,000 cement sacks a month")

    def test_the_board_and_the_warm_lead(self):
        page = self.sign_in(self.manager, "/app/sales/board")
        expect(page.get_by_role("heading", name="Pipeline board")).to_be_visible()
        column = page.get_by_role("region", name="New")
        expect(column).to_contain_text("20,000 sacks a month")
        column.get_by_role("button", name="Move to qualified").click()
        self.toast(page, "moved to qualified")
        self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.stage, "qualified")
        expect(page.get_by_role("region", name="Qualified")).to_contain_text("20,000 sacks a month")

        page.goto(f"{self.live_server_url}/app/sales/leads/{self.lead.pk}")
        expect(page.get_by_text(re.compile(r"\d+ of 100: from exhibition \+20"))).to_be_visible()
        self.assertEqual(self.problems, [])
