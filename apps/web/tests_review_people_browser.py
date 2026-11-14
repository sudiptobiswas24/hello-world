"""review_people: one flow as the Payroll Officer and one as the Controller, read for what the screen says."""

import datetime
import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover
    expect = None

from .tests_browser import BrowserTestCase
from . import tests_browser_people as people_tests


class ReviewPayrollScreens(BrowserTestCase):
    def setUp(self):
        super().setUp()
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company

        self.net_pay = Account.objects.create(code="2390", name="Net pay payable", account_type=AccountType.LIABILITY)
        self.bank = Account.objects.create(code="1090", name="Salary bank", account_type=AccountType.ASSET, holds_money=True)
        self.wages = Account.objects.create(code="6090", name="Wages", account_type=AccountType.EXPENSE)
        company = Company.get()
        company.net_pay_account = self.net_pay
        company.save()
        self.currency = company.base_currency

    def staff(self, number, amount):
        from decimal import Decimal

        from apps.hr.pay_details import EmployeePayDetails
        from apps.hr.payroll import ComponentKind, EmployeeCompensation, PayComponent

        salary = PayComponent.objects.get_or_create(code="SAL", defaults={
            "name": "Salary", "kind": ComponentKind.EARNING, "expense_account": self.wages, "sequence": 10})[0]
        person = people_tests.PeopleInTheBrowserTests.employee(self, number, f"{number} Das", None)
        EmployeeCompensation.objects.create(employee=person, component=salary, amount=Decimal(amount),
                                            effective_from=datetime.date(2026, 1, 1))
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

    def test_Q1_the_officer_writes_the_file_the_controller_pays_it_and_the_screen_tells_the_truth_after_a_void(self):
        people = [self.staff(code, amount) for code, amount in (("E1", "123456.78"), ("E2", "8450.50"), ("E3", "3375"))]
        run = self.june()
        officer = self.sign_in(self.person("Payroll Officer"), f"/app/payroll/runs/{run.pk}")
        expect(officer.get_by_role("button", name="Write bank file")).to_be_visible()
        # What the Payroll Officer is not offered on the posted run: the Controller's void.
        expect(officer.get_by_role("button", name=re.compile("Void"))).to_have_count(0)
        expect(officer.get_by_text("1,35,282.28").first).to_be_visible()
        shown = officer.locator("body").inner_text()
        self.assertNotIn("135282.28", shown, "a raw exact string reached the page")
        officer.get_by_role("button", name="Write bank file").click()
        self.toast(officer, "Bank file written")
        officer.wait_for_url(re.compile(r"/payroll/salary-files/\d+$"))
        expect(officer.get_by_text("Not paid").first).to_be_visible()
        expect(officer.get_by_role("button", name="Mark paid")).to_have_count(0)
        body = officer.locator("body").inner_text()
        self.assertIn("1,35,282.28", body)
        from apps.hr.bank_file import SalaryTransferFile

        made = SalaryTransferFile.objects.get()
        bulk = self.money(people[0].party, "135282.28")
        controller = self.sign_in(self.person("Controller"), f"/app/payroll/salary-files/{made.pk}", page=self.new_page())
        controller.get_by_role("button", name="Mark paid", exact=True).click()
        form = controller.get_by_role("form", name="Mark paid")
        self.pick(controller, form, "Paid by", bulk.number)
        form.get_by_role("button", name="Mark paid", exact=True).click()
        self.toast(controller, "Marked paid")
        # The payment is voided (a wrong bank picked): every slip is owed again.
        bulk.void(on_date=datetime.date(2026, 7, 1))
        self.assertEqual([slip.is_paid() for slip in run.payslips.all()], [False, False, False])
        controller.reload()
        expect(controller.get_by_role("heading", name=re.compile("Bank file")).first).to_be_visible()
        text = controller.locator("body").inner_text()
        self.assertIn("Not paid", text, "the file's screen says it is paid; its payment is voided and nobody is paid")
        self.problems = [p for p in self.problems if not p.startswith("400 ") and "status of 400" not in p]
        self.assertEqual(self.problems, [])

    def test_Q2_an_advance_balance_is_money_formatted_by_the_app(self):
        from decimal import Decimal

        from apps.accounting.models import Account, AccountType
        from apps.hr.advances import SalaryAdvance
        from apps.hr.payroll import ComponentBasis, ComponentKind, PayComponent

        advances = Account.objects.create(code="1390", name="Employee advances", account_type=AccountType.ASSET)
        component = PayComponent.objects.create(code="ADV", name="Advance recovered", kind=ComponentKind.DEDUCTION,
                                                basis=ComponentBasis.FIXED, liability_account=advances, sequence=80)
        person = self.staff("A1", "120000")
        given = self.money(person.party, "150000", counterpart=advances, day=datetime.date(2026, 5, 20))
        advance = SalaryAdvance.objects.create(employee=person, payment=given, component=component,
                                               amount=Decimal("150000"), instalment=Decimal("20000"),
                                               recover_from=datetime.date(2026, 6, 1))
        controller = self.sign_in(self.person("Controller"), f"/app/payroll/advances/{advance.pk}")
        expect(controller.get_by_role("button", name="Void", exact=True)).to_be_visible()
        text = controller.locator("body").inner_text()
        left = re.findall(r"Left [^\n]*", text)
        self.assertEqual(left, ["Left 1,50,000.00"], f"the advance's balance as shown: {left}")
