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


class MyPayslipsInTheBrowserTests(BrowserTestCase):
    """Riley reads Riley's payslips, and only Riley's; the run is June's, posted."""

    def test_an_employee_sees_their_own_payslips_and_nobody_elses(self):
        from decimal import Decimal

        from apps.hr.payroll import ComponentKind, EmployeeCompensation, PayComponent, PayRun

        Account.objects.create(code="2300", name="Net pay payable", account_type=AccountType.LIABILITY)
        from apps.core.models import Company

        company = Company.get()
        company.net_pay_account = Account.objects.get(code="2300")
        company.save()
        wages = Account.objects.create(code="6000", name="Wages", account_type=AccountType.EXPENSE)
        salary = PayComponent.objects.create(code="SAL", name="Salary", kind=ComponentKind.EARNING, expense_account=wages)
        riley = PeopleInTheBrowserTests.employee(self, "E005", "Riley Chen", self.person("Employee Self Service"))
        other = PeopleInTheBrowserTests.employee(self, "E007", "Sam Roy", None)
        for person, amount in ((riley, "12000"), (other, "15550")):
            EmployeeCompensation.objects.create(employee=person, component=salary, amount=Decimal(amount),
                                                effective_from=datetime.date(2026, 1, 1))
        run = PayRun.objects.create(period_start=datetime.date(2026, 6, 1), period_end=datetime.date(2026, 6, 30),
                                    pay_date=datetime.date(2026, 6, 30))
        run.calculate()
        run.post()
        page = self.sign_in(riley.user, "/app/payroll/my-payslips")
        expect(page.get_by_role("heading", name="My payslips")).to_be_visible()
        expect(self.rows()).to_have_count(1)
        expect(self.rows().first).to_contain_text("12,000.00")
        expect(page.locator("tbody")).not_to_contain_text("15,550.00")
        expect(page.get_by_role("link", name="PDF")).to_have_attribute(
            "href", f"/api/hr/my-payslips/{run.payslips.get(employee=riley).pk}/pdf/")
        self.assertEqual(self.problems, [])

    def test_the_professional_tax_register_reads_what_the_run_kept(self):
        from decimal import Decimal

        from apps.core.models import Company
        from apps.hr.payroll import (ComponentBasis, ComponentKind, EmployeeCompensation, PayComponent,
                                     PayComponentSlab, PayRun, Statutory)

        net_pay = Account.objects.create(code="2300", name="Net pay payable", account_type=AccountType.LIABILITY)
        company = Company.get()
        company.net_pay_account = net_pay
        company.save()
        wages = Account.objects.create(code="6000", name="Wages", account_type=AccountType.EXPENSE)
        owed = Account.objects.create(code="2330", name="PT payable", account_type=AccountType.LIABILITY)
        salary = PayComponent.objects.create(code="SAL", name="Salary", kind=ComponentKind.EARNING, expense_account=wages,
                                             sequence=10)
        # After the salary: a slab is taken of the gross worked out before it.
        pt = PayComponent.objects.create(code="PT", name="Professional tax", kind=ComponentKind.DEDUCTION,
                                         basis=ComponentBasis.SLAB, liability_account=owed, statutory=Statutory.PT,
                                         sequence=90)
        PayComponentSlab.objects.create(component=pt, state="WB", above=Decimal("15000"), amount=Decimal("130"),
                                        effective_from=datetime.date(2026, 4, 1))
        worker = PeopleInTheBrowserTests.employee(self, "E008", "Mita Pal", None)
        EmployeeCompensation.objects.create(employee=worker, component=salary, amount=Decimal("20000"),
                                            effective_from=datetime.date(2026, 1, 1))
        EmployeeCompensation.objects.create(employee=worker, component=pt, amount=Decimal("1"),
                                            effective_from=datetime.date(2026, 1, 1))
        run = PayRun.objects.create(period_start=datetime.date(2026, 6, 1), period_end=datetime.date(2026, 6, 30),
                                    pay_date=datetime.date(2026, 6, 30))
        run.calculate()
        run.post()
        page = self.sign_in(self.person("Payroll Officer"), "/app/payroll/registers")
        page.locator("input[type=month]").fill("2026-06")
        expect(page.get_by_role("heading", name="Statutory registers")).to_be_visible()
        expect(self.rows()).to_have_count(1)
        expect(self.rows().first).to_contain_text("E008")
        expect(self.rows().first).to_contain_text("130.00")
        expect(self.rows().first).to_contain_text("WB from 2026-04-01")
        self.assertEqual(self.problems, [])


class PayrollScreensInTheBrowserTests(BrowserTestCase):
    """The bank file, an advance, bank details and a statutory figure, each from the screen as the people who do it."""

    def setUp(self):
        super().setUp()
        from apps.core.models import Company

        self.net_pay = Account.objects.create(code="2390", name="Net pay payable", account_type=AccountType.LIABILITY)
        self.bank = Account.objects.create(code="1090", name="Salary bank", account_type=AccountType.ASSET, holds_money=True)
        self.wages = Account.objects.create(code="6090", name="Wages", account_type=AccountType.EXPENSE)
        company = Company.get()
        company.net_pay_account = self.net_pay
        company.save()
        self.currency = company.base_currency

    def staff(self, number, amount, banked=True):
        from decimal import Decimal

        from apps.hr.pay_details import EmployeePayDetails
        from apps.hr.payroll import ComponentKind, EmployeeCompensation, PayComponent

        salary = PayComponent.objects.get_or_create(code="SAL", defaults={
            "name": "Salary", "kind": ComponentKind.EARNING, "expense_account": self.wages, "sequence": 10})[0]
        person = PeopleInTheBrowserTests.employee(self, number, f"{number} Das", None)
        EmployeeCompensation.objects.create(employee=person, component=salary, amount=Decimal(amount),
                                            effective_from=datetime.date(2026, 1, 1))
        if banked:
            EmployeePayDetails.objects.create(employee=person, account_holder=f"{number} Das",
                                              account_number=f"3001{person.pk:08d}", ifsc="SBIN0001234")
        return person

    def june(self):
        from apps.hr.payroll import PayRun

        run = PayRun.objects.create(period_start=datetime.date(2026, 6, 1), period_end=datetime.date(2026, 6, 30),
                                    pay_date=datetime.date(2026, 6, 30))
        run.calculate()
        run.post()
        return run

    def money(self, party, amount, counterpart=None, direction="disbursement", day=datetime.date(2026, 6, 30)):
        from decimal import Decimal

        from apps.accounting.models import Payment

        payment = Payment.objects.create(party=party, direction=direction, payment_date=day, amount=Decimal(amount),
                                         currency=self.currency, bank_account=self.bank,
                                         counterpart_account=counterpart or self.net_pay)
        payment.post()
        return Payment.objects.get(pk=payment.pk)

    def pick(self, page, form, label, text):
        form.get_by_role("combobox", name=label).fill(text)
        page.get_by_role("option", name=re.compile(re.escape(text))).first.click()

    def test_the_bank_file_written_paid_and_one_transfer_returned(self):
        from apps.hr.bank_file import SalaryTransferFile

        people = [self.staff(code, amount) for code, amount in (("E1", "12000"), ("E2", "8450.50"), ("E3", "3375"))]
        run = self.june()
        officer = self.sign_in(self.person("Payroll Officer"), f"/app/payroll/runs/{run.pk}")
        officer.get_by_role("button", name="Write bank file").click()
        self.toast(officer, "Bank file written")
        officer.wait_for_url(re.compile(r"/payroll/salary-files/\d+$"))
        made = SalaryTransferFile.objects.get()
        self.assertEqual(made.total, 23825.50)
        expect(officer.get_by_role("button", name="Mark paid")).to_have_count(0)

        bulk = self.money(people[0].party, "23825.50")
        controller = self.sign_in(self.person("Controller"), f"/app/payroll/salary-files/{made.pk}", page=self.new_page())
        controller.get_by_role("button", name="Mark paid", exact=True).click()
        form = controller.get_by_role("form", name="Mark paid")
        self.pick(controller, form, "Paid by", bulk.number)
        form.get_by_role("button", name="Mark paid", exact=True).click()
        self.toast(controller, "Marked paid")

        receipt = self.money(people[1].party, "8450.50", direction="receipt")
        controller.get_by_role("button", name="Return a transfer", exact=True).click()
        form = controller.get_by_role("form", name="Return a transfer")
        form.get_by_label("Transfer").select_option(str(made.rows.get(payslip__employee=people[1]).pk))
        self.pick(controller, form, "Received back by", receipt.number)
        form.get_by_role("button", name="Return a transfer", exact=True).click()
        self.toast(controller, "Returned")
        self.assertEqual([slip.is_paid() for slip in run.payslips.order_by("employee__employee_number")],
                         [True, False, True])
        self.assertEqual(self.problems, [])

    def test_an_advance_given_recovered_by_a_run_and_voided(self):
        from apps.hr.advances import SalaryAdvance
        from apps.hr.payroll import ComponentBasis, ComponentKind, PayComponent, PayslipLine

        advances = Account.objects.create(code="1390", name="Employee advances", account_type=AccountType.ASSET)
        PayComponent.objects.create(code="ADV", name="Advance recovered", kind=ComponentKind.DEDUCTION,
                                    basis=ComponentBasis.FIXED, liability_account=advances, sequence=80)
        person = self.staff("A1", "12000")
        given = self.money(person.party, "5000", counterpart=advances, day=datetime.date(2026, 5, 20))
        # Given by whoever pays it out: picking the payment is reading the ledger (the Controller).
        controller = self.sign_in(self.person("Controller"), "/app/payroll/advances/new")
        officer = controller
        self.pick(officer, officer, "Employee", "A1")
        self.pick(officer, officer, "Paid by", given.number)
        self.pick(officer, officer, "Recovered by", "ADV")
        officer.get_by_label("Amount").fill("5000")
        officer.get_by_label("Instalment").fill("2000")
        officer.get_by_label("Recover from").fill("2026-06-01")
        officer.get_by_role("button", name="Create", exact=True).click()
        officer.wait_for_url(re.compile(r"/payroll/advances/\d+$"))
        advance = SalaryAdvance.objects.get()
        run = self.june()
        self.assertEqual([line.amount for line in PayslipLine.objects.filter(advance=advance)], [2000])

        controller.goto(f"{self.live_server_url}/app/payroll/advances/{advance.pk}")
        controller.get_by_role("button", name="Void", exact=True).click()
        form = controller.get_by_role("form", name="Void")
        form.get_by_label("Voided on").fill("2026-07-01")
        form.get_by_role("button", name="Void", exact=True).click()
        expect(controller.get_by_text(re.compile("Void that run first")).first).to_be_visible()
        run.void(on_date=datetime.date(2026, 7, 1))
        controller.reload()
        controller.get_by_role("button", name="Void", exact=True).click()
        form = controller.get_by_role("form", name="Void")
        form.get_by_label("Voided on").fill("2026-07-01")
        form.get_by_role("button", name="Void", exact=True).click()
        self.toast(controller, "Voided")
        advance.refresh_from_db()
        self.assertIsNotNone(advance.voided_at)
        # The one refusal asked for above is a 400, in the console and in the responses; nothing else.
        self.problems = [p for p in self.problems if not p.startswith("400 ") and "status of 400" not in p]
        self.assertEqual(self.problems, [])

    def test_bank_details_entered_by_payroll_and_not_offered_to_a_line_manager(self):
        from apps.hr.pay_details import EmployeePayDetails

        person = self.staff("E1", "12000", banked=False)
        officer = self.sign_in(self.person("Payroll Officer"), "/app/payroll/pay-details/new")
        self.pick(officer, officer, "Employee", "E1")
        officer.get_by_label("Account holder").fill("E1 Das")
        officer.get_by_label("Account number").fill("300100001111")
        officer.get_by_label("IFSC").fill("sbin0001234")
        officer.get_by_role("button", name="Create", exact=True).click()
        officer.wait_for_url(re.compile(r"/payroll/pay-details/\d+$"))
        self.assertEqual(EmployeePayDetails.objects.get(employee=person).ifsc, "SBIN0001234")
        manager = self.sign_in(self.person("Line Manager"), "/app/payroll/employees", page=self.new_page())
        expect(manager.get_by_role("heading", name="Employees")).to_be_visible()
        expect(manager.get_by_role("link", name="Bank and PAN")).to_have_count(0)
        self.assertEqual(self.problems, [])

    def test_a_statutory_figure_entered_on_screen(self):
        from apps.hr.statutory import StatutoryFigure

        page = self.sign_in(self.person("Payroll Officer"), "/app/payroll/statutory-figures/new")
        page.get_by_label("Figure").select_option("epf_ceiling")
        page.get_by_label("Applies from").fill("2026-09-17")
        page.get_by_label("Value").fill("25000")
        page.get_by_label("Source").fill("S.O. 5109(E), to confirm")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/payroll/statutory-figures/\d+$"))
        figure = StatutoryFigure.objects.get()
        self.assertEqual((figure.kind, figure.effective_from, figure.value), ("epf_ceiling", datetime.date(2026, 9, 17), 25000))
        self.assertEqual(self.problems, [])
