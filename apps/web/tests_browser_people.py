"""
Riley claims the train fare and submits; Jordan, Riley's manager,
approves it; the bookkeeper pays it from cash. The personnel office
opens a job and hires the applicant offered it.
"""

import datetime
import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.models import Account, AccountType
from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.hr.models import Employee, ExpenseClaim
from apps.hr.recruitment import Applicant, JobOpening

from .tests_browser import BrowserTestCase


class PeopleInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.travel = Account.objects.create(code="6200", name="Travel", account_type=AccountType.EXPENSE)
        self.cash = Account.objects.create(code="1000", name="Cash in hand", account_type=AccountType.ASSET, holds_money=True)
        self.jordan = self.employee("E006", "Jordan Park", self.person("Line Manager"))
        self.riley = self.employee("E005", "Riley Chen", self.person("Employee Self Service"), manager=self.jordan)

    def employee(self, number, name, user, manager=None):
        party = Party.objects.create(code=number, name=name)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        return Employee.objects.create(party=party, employee_number=number, hire_date=datetime.date(2025, 1, 1), user=user, manager=manager)

    def test_a_claim_from_riley_to_jordan_to_the_books(self):
        page = self.sign_in(self.riley.user, "/app/payroll/claims/new")
        page.get_by_label("For").fill("Customer visit, Pune")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/payroll/claims/\d+$"))
        page.get_by_role("button", name="Add a line").click()
        form = page.get_by_role("form", name="Add a line")
        form.get_by_label("Spent on").fill("2026-06-01")
        form.get_by_label("What").fill("Train to Pune")
        form.get_by_role("combobox", name="Account").fill("6200")
        page.get_by_role("option", name=re.compile("6200")).click()
        form.get_by_label("Amount").fill("300")
        form.get_by_role("button", name="Add a line").click()
        self.toast(page, "Added")
        page.get_by_role("button", name="Submit", exact=True).click()
        self.toast(page, "Submitted")
        claim = ExpenseClaim.objects.get()
        self.assertEqual((claim.employee, claim.status, claim.total()), (self.riley, "submitted", 300))

        jordan = self.sign_in(self.jordan.user, f"/app/payroll/claims/{claim.pk}", page=self.new_page())
        jordan.get_by_role("button", name="Approve", exact=True).click()
        jordan.get_by_role("form", name="Approve").get_by_role("button", name="Approve", exact=True).click()
        self.toast(jordan, "Approved")

        books = self.sign_in(self.person("Bookkeeper"), f"/app/payroll/claims/{claim.pk}", page=self.new_page())
        books.get_by_role("button", name="Pay", exact=True).click()
        pay = books.get_by_role("form", name="Pay")
        pay.get_by_role("combobox", name="From account").fill("1000")
        books.get_by_role("option", name=re.compile("1000")).click()
        pay.get_by_role("button", name="Pay", exact=True).click()
        self.toast(books, "Paid and posted")
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.paid_from, claim.journal_entry.posted), ("paid", self.cash, True))
        self.assertEqual(self.problems, [])

    def test_an_opening_and_a_hire(self):
        opening = JobOpening.objects.create(title="Loom operator", openings=1, opened_on=datetime.date(2026, 6, 1))
        asha = Applicant.objects.create(opening=opening, name="Asha Devi", applied_on=datetime.date(2026, 6, 2), stage="offered")
        page = self.sign_in(self.person("HR Admin"), f"/app/payroll/applicants/{asha.pk}")
        page.get_by_role("button", name="Hire", exact=True).click()
        form = page.get_by_role("form", name="Hire")
        form.get_by_label("Employee number").fill("E101")
        form.get_by_label("Joins on").fill("2026-07-01")
        form.get_by_role("button", name="Hire", exact=True).click()
        page.wait_for_url(re.compile(r"/payroll/employees/\d+$"))
        employee = Employee.objects.get(employee_number="E101")
        self.assertEqual((employee.party.name, employee.job_title, employee.hire_date), ("Asha Devi", "Loom operator", datetime.date(2026, 7, 1)))
        expect(page.get_by_text("Asha Devi").first).to_be_visible()
        self.assertEqual(self.problems, [])
