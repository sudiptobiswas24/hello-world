"""
The AR Manager makes a team, puts a rep in it, sets it a target for
September, and reads the target against what the rep's customer was
billed: 1,250.50 of 2,000, short by 749.50.
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.hr.models import Employee
from apps.sales.models import CustomerProfile, Invoice, InvoiceLine, SalesRep
from apps.sales.teams import SalesTarget, SalesTeam

from .tests_browser import BrowserTestCase


class TeamsInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        person = Party.objects.create(code="PRIYA", name="Priya Nair")
        PartyRoleAssignment.objects.create(party=person, role=PartyRole.EMPLOYEE)
        Employee.objects.create(party=person, employee_number="PRIYA", hire_date=datetime.date(2026, 1, 1))
        self.priya = SalesRep.objects.create(party=person)
        CustomerProfile.objects.create(party=self.customer, sales_rep=person)
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 9, 8),
                                         receivable_account=self.ar)
        InvoiceLine.objects.create(invoice=invoice, item=self.item, description="Sacks", quantity=Decimal("1"),
                                   unit_price=Decimal("1250.50"), revenue_account=self.revenue)
        invoice.post()

    def test_a_team_its_rep_its_target_and_the_report(self):
        page = self.sign_in(self.person("AR Manager"), "/app/sales/teams/new")
        page.get_by_label("Code").fill("WEST")
        page.get_by_label("Team", exact=True).fill("West")
        page.get_by_label("Leader").select_option(str(self.priya.pk))
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/sales/teams/\d+$"))
        west = SalesTeam.objects.get(code="WEST")
        self.assertEqual(west.leader, self.priya)

        page.goto(f"{self.live_server_url}/app/sales/reps/{self.priya.pk}")
        page.get_by_label("Team", exact=True).select_option(str(west.pk))
        page.get_by_role("button", name="Save", exact=True).click()
        self.toast(page, "Saved")
        self.priya.refresh_from_db()
        self.assertEqual(self.priya.team, west)

        page.goto(f"{self.live_server_url}/app/sales/targets/new")
        page.get_by_label("Team", exact=True).select_option(str(west.pk))
        page.get_by_label("From").fill("2026-09-01")
        page.get_by_label("To", exact=True).fill("2026-09-30")
        page.get_by_label("Target, net of tax").fill("2000")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/sales/targets/\d+$"))
        self.assertEqual(SalesTarget.objects.get(team=west).amount, Decimal("2000"))

        page.goto(f"{self.live_server_url}/app/sales/targets-report?from=2026-09-01&to=2026-09-30")
        row = page.locator("tbody tr").first
        expect(row).to_contain_text("West (team)")
        expect(row).to_contain_text("2,000.00")
        expect(row).to_contain_text("1,250.50")
        expect(row).to_contain_text("749.50")
        expect(row).to_contain_text("62.5")
        self.assertEqual(self.problems, [])
