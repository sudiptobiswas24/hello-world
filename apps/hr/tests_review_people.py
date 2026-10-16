"""
review_people probes for gate g21: payroll's go-live outputs at fbfc49d.

Each test states a fact about money or state that SHOULD be true. A test that
fails is a finding; the finding number is in the test's docstring. These are
probes, not the regression tests: they stay unpushed in the review worktree.
"""

import csv
import datetime
import io
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, JournalLine, Payment, PaymentDirection

from . import tests_pay_outputs as base
from .advances import SalaryAdvance
from .attendance import AttendanceDay, AttendanceStatus
from .bank_file import SalaryTransferFile
from .pay_details import EmployeePayDetails
from .payroll import (
    ComponentBasis,
    ComponentKind,
    PayComponent,
    PayComponentSlab,
    PayRun,
    PayslipLine,
    Statutory,
)
from .statutory import FigureKind, StatutoryFigure
from .statutory_files import ecr_rows, lwf_register, pt_register
from .tests_dated_figures import DatedFiguresTestCase, month

D = datetime.date
JULY1 = D(2026, 7, 1)


def refused(call, *args, **kwargs):
    """True when the call was refused in words (a ValidationError)."""
    try:
        call(*args, **kwargs)
    except ValidationError:
        return True
    return False


class TheBankFile(base.OutputsTestCase):
    def setUp(self):
        super().setUp()
        self.people = [self.employee(code) for code in ("E1", "E2", "E3")]
        for person, amount in zip(self.people, ("12000", "8450.50", "3375")):
            self.pay(person, self.salary, amount)
            self.banked(person)
        self.posted = self.june()

    def owed(self):
        """What net pay payable says the company owes, credit-normal."""
        return -self.balance(self.net_pay)

    def paid_with_one_transfer_returned(self):
        written = SalaryTransferFile.prepare(self.posted)
        first = self.money(self.people[0].party, "23825.50")
        written.mark_paid(first)
        row = written.rows.get(payslip__employee=self.people[1])
        receipt = self.money(self.people[1].party, "8450.50", direction=PaymentDirection.RECEIPT)
        row.return_transfer(receipt)
        return written, first, row, receipt

    def test_F1_a_return_receipt_is_not_voided_once_the_slip_was_paid_again(self):
        """E2's transfer comes back (receipt R), E2 is paid by a payment of their own, then R is voided."""
        _written, _first, _row, receipt = self.paid_with_one_transfer_returned()
        self.posted.payslips.get(employee=self.people[1]).pay(self.money(self.people[1].party, "8450.50"))
        self.assertEqual(self.owed(), Decimal("0.00"))
        if refused(receipt.void, on_date=JULY1):
            return
        # Not refused: E2 now reads paid once, so the control account must read nothing owed.
        self.assertEqual((self.owed(), self.posted.unpaid_net()), (Decimal("0.00"), Decimal("0")),
                         "E2 was paid twice (transfer 1 stood after all, transfer 2 by their own payment)")

    def test_F2_the_files_payment_is_not_voided_under_a_standing_return(self):
        """The file is paid, E2's 8,450.50 comes back (receipt R stands), then the file's payment is voided."""
        _written, first, _row, _receipt = self.paid_with_one_transfer_returned()
        if refused(first.void, on_date=JULY1):
            return
        self.assertEqual(self.owed(), self.posted.unpaid_net(),
                         "net pay payable and the slips disagree after the file's payment was voided")

    def test_F3_a_return_must_come_from_the_person_whose_transfer_it_was(self):
        """A receipt in E3's name cannot return E2's transfer."""
        written = SalaryTransferFile.prepare(self.posted)
        written.mark_paid(self.money(self.people[0].party, "23825.50"))
        row = written.rows.get(payslip__employee=self.people[1])
        stranger = self.money(self.people[2].party, "8450.50", direction=PaymentDirection.RECEIPT)
        self.assertTrue(refused(row.return_transfer, stranger))

    def test_F4_the_csv_total_is_the_posted_net_pay_to_the_paisa(self):
        """A July run with a taxed fourth person: rows, total, the run's net and the journal's net pay credit agree."""
        fourth = self.employee("E4")
        self.pay(fourth, self.salary, "12345.67")
        self.pay(fourth, self.tax, "7.33")
        self.banked(fourth)
        july = self.pay_run(D(2026, 7, 1), D(2026, 7, 31), pay_date=D(2026, 7, 31))
        july.calculate()
        july.post()
        written = SalaryTransferFile.prepare(july)
        table = list(csv.reader(io.StringIO(written.text)))
        rows, total = table[1:-1], table[-1]
        posted_credit = sum((l.credit for l in JournalLine.objects.filter(entry=july.journal_entry,
                                                                           account=self.net_pay)), Decimal("0"))
        self.assertEqual((sum((Decimal(r[3]) for r in rows), Decimal("0")), Decimal(total[3]), written.total,
                          july.net()), (posted_credit,) * 4)

    def test_F5_the_file_keeps_the_account_it_was_written_with(self):
        written = SalaryTransferFile.prepare(self.posted)
        before = written.text
        details = EmployeePayDetails.objects.get(employee=self.people[1])
        details.account_number, details.ifsc = "999900001111", "HDFC0000001"
        details.save()
        EmployeePayDetails.objects.filter(employee=self.people[0]).delete()
        written.refresh_from_db()
        client = self.as_role("Payroll Officer")
        served = client.get(f"/api/hr/salary-files/{written.pk}/csv/").content.decode()
        self.assertEqual((written.text, served), (before, before))
        self.assertEqual(list(written.rows.filter(payslip__employee=self.people[1]).values_list(
            "account_number", "ifsc")), [(f"3001{self.people[1].pk:08d}", "SBIN0001234")])

    def test_F6_a_voided_runs_file_cannot_be_marked_paid(self):
        written = SalaryTransferFile.prepare(self.posted)
        self.posted.void(on_date=JULY1)
        self.assertTrue(refused(written.mark_paid, self.money(self.people[0].party, "23825.50")))
        self.assertTrue(refused(SalaryTransferFile.prepare, self.posted))

    def test_F7_no_slip_is_paid_twice_through_files_and_hand_payments(self):
        written = SalaryTransferFile.prepare(self.posted)
        self.assertTrue(refused(SalaryTransferFile.prepare, self.posted), "a second unpaid file for the run")
        self.posted.payslips.get(employee=self.people[1]).pay(self.money(self.people[1].party, "8450.50"))
        stray = self.money(self.people[0].party, "23825.50")
        self.assertTrue(refused(written.mark_paid, stray), "the file was written before E2 was paid by hand")
        stray.void(on_date=JULY1)
        written.delete()
        again = SalaryTransferFile.prepare(self.posted)
        self.assertEqual((again.total, again.rows.count()), (Decimal("15375.00"), 2))
        again.mark_paid(self.money(self.people[0].party, "15375.00"))
        self.assertEqual((self.owed(), self.posted.unpaid_net()), (Decimal("0.00"), Decimal("0")))
        self.assertTrue(refused(SalaryTransferFile.prepare, self.posted), "everybody is paid")

    def test_F8_the_files_payment_voided_and_the_file_rewritten_pays_once(self):
        written = SalaryTransferFile.prepare(self.posted)
        first = self.money(self.people[0].party, "23825.50")
        written.mark_paid(first)
        first.void(on_date=JULY1)
        self.assertEqual((self.owed(), self.posted.unpaid_net()), (Decimal("23825.50"), Decimal("23825.50")))
        stray = self.money(self.people[0].party, "23825.50")
        self.assertTrue(refused(written.mark_paid, stray))
        stray.void(on_date=JULY1)
        again = SalaryTransferFile.prepare(self.posted)
        again.mark_paid(self.money(self.people[0].party, "23825.50"))
        self.assertEqual((self.owed(), self.posted.unpaid_net()), (Decimal("0.00"), Decimal("0")))

    def test_F9_a_transfer_returned_unpays_one_slip_and_books_the_money_back(self):
        _written, _first, row, receipt = self.paid_with_one_transfer_returned()
        self.assertEqual([slip.employee.employee_number for slip in self.posted.payslips.all() if not slip.is_paid()],
                         ["E2"])
        self.assertEqual((self.owed(), self.posted.unpaid_net()), (Decimal("8450.50"),) * 2)
        receipt.void(on_date=JULY1)
        self.assertEqual((self.owed(), self.posted.unpaid_net()), (Decimal("0.00"), Decimal("0")))
        self.assertTrue(refused(row.return_transfer, receipt), "a voided receipt returns nothing")

    def test_F10_a_name_that_is_a_spreadsheet_formula_is_not_written_as_one(self):
        """The CSV goes to a bank portal and is opened in a spreadsheet: a holder named =HYPERLINK(...) is data."""
        details = EmployeePayDetails.objects.get(employee=self.people[2])
        details.account_holder = '=HYPERLINK("http://x","pay")'
        details.save()
        written = SalaryTransferFile.prepare(self.posted)
        cells = [cell for row in csv.reader(io.StringIO(written.text)) for cell in row]
        self.assertFalse([cell for cell in cells if cell[:1] in "=+-@" and cell not in ("",)
                          and not cell.replace(".", "", 1).isdigit()])


class TheAdvances(base.OutputsTestCase):
    def setUp(self):
        super().setUp()
        self.person = self.employee("A1")
        self.pay(self.person, self.salary, "12000")

    def give(self, amount="5000", instalment="2000", person=None):
        person = person or self.person
        payment = self.money(person.party, amount, counterpart=self.advances, day=D(2026, 5, 20))
        return SalaryAdvance.objects.create(employee=person, payment=payment, component=self.recovery,
                                            amount=Decimal(amount), instalment=Decimal(instalment),
                                            recover_from=D(2026, 6, 1))

    def month(self, number, post=True, first=None, last=None):
        first = first or D(2026, number, 1)
        last = last or (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        run = self.pay_run(first, last, pay_date=last)
        run.calculate()
        if post:
            run.post()
        return run

    def test_A1_the_receivable_foots_to_the_derived_balance_after_every_step(self):
        advance = self.give()
        seen = [("given", self.balance(self.advances), advance.balance())]
        june, july = self.month(6), self.month(7)
        seen.append(("june and july", self.balance(self.advances), advance.balance()))
        july.void(on_date=D(2026, 8, 1))
        seen.append(("july voided", self.balance(self.advances), advance.balance()))
        july_again = self.month(7)
        seen.append(("july again", self.balance(self.advances), advance.balance()))
        self.month(8)
        seen.append(("august", self.balance(self.advances), advance.balance()))
        june.void(on_date=D(2026, 9, 1))
        seen.append(("after a try at voiding june", self.balance(self.advances), advance.balance()))
        self.assertEqual([step for step in seen if step[1] != step[2]], [])
        self.assertEqual(july_again.status, "posted")

    def test_A2_the_receivable_is_not_repointed_under_a_standing_advance(self):
        """The advance was given against account 1300; the component's account is changed; a run recovers."""
        advance = self.give()
        new = Account.objects.create(code="1301", name="Employee advances (new)", account_type=AccountType.ASSET)
        self.recovery.liability_account = new
        if refused(self.recovery.save):
            return
        self.month(6)
        self.assertEqual(self.balance(self.advances), advance.balance(),
                         "the receivable the advance was given into no longer foots to its balance")

    def test_A3_an_advance_whose_payment_was_voided_alone_is_not_owed(self):
        advance = self.give()
        if refused(advance.payment.void, on_date=D(2026, 5, 25)):
            return
        self.assertEqual((self.balance(self.advances), advance.balance()), (Decimal("0.00"), Decimal("0.00")),
                         "the ledger gave nothing; the advance still reads a balance")

    def test_A4_a_leavers_final_slip_takes_within_the_cap_and_the_rest_stays_owed_on_the_receivable(self):
        """12,000 earned, tax 10% = 1,200; the cap leaves 6,000 - 1,200 = 4,800 of 9,000; 4,200 stays owed."""
        self.pay(self.person, self.tax, "10")
        self.person.termination_date = D(2026, 6, 30)
        self.person.employment_status = "terminated"
        self.person.save()
        advance = self.give("9000", "2000")
        june = self.month(6)
        slip = june.payslips.get()
        self.assertEqual((slip.net(), advance.balance(), self.balance(self.advances)),
                         (Decimal("6000.00"), Decimal("4200.00"), Decimal("4200.00")))
        june.void(on_date=JULY1)
        self.assertEqual((advance.balance(), self.balance(self.advances)), (Decimal("9000.00"),) * 2)

    def test_A5_an_advance_is_voided_only_after_the_run_that_recovered_it_is_voided_and_the_books_return(self):
        advance = self.give()
        june = self.month(6)
        self.assertTrue(refused(advance.void, on_date=JULY1))
        self.assertTrue(refused(Payment.objects.get(pk=advance.payment_id).void, on_date=JULY1))
        june.void(on_date=JULY1)
        SalaryAdvance.objects.get(pk=advance.pk).void(on_date=JULY1)
        self.assertEqual((self.balance(self.advances), self.balance(self.bank)), (Decimal("0.00"), Decimal("0.00")))

    def test_A6_a_run_calculated_before_the_advance_was_voided_cannot_post_the_recovery(self):
        advance = self.give()
        june = self.month(6, post=False)
        advance.void(on_date=D(2026, 6, 1))
        self.assertTrue(refused(june.post))
        self.assertEqual(self.balance(self.advances), Decimal("0.00"))

    def test_A7_the_instalment_is_one_a_month_when_the_month_is_paid_in_two_runs(self):
        """OWNER QUESTION, not a defect while the field says 'what each pay run takes back'."""
        self.give("5000", "2000")
        self.month(6, first=D(2026, 6, 1), last=D(2026, 6, 15))
        self.month(6, first=D(2026, 6, 16), last=D(2026, 6, 30))
        taken = sum((l.amount for l in PayslipLine.objects.filter(advance__isnull=False)), Decimal("0"))
        self.assertEqual(taken, Decimal("2000.00"))

    def test_A8_a_run_posted_with_the_advances_payment_voided_in_between_is_refused(self):
        advance = self.give()
        june = self.month(6, post=False)
        advance.payment.void(on_date=D(2026, 6, 2))
        self.assertTrue(refused(june.post))
        self.assertEqual(self.balance(self.advances), Decimal("0.00"))


class TheLawsFigures(DatedFiguresTestCase):
    def setUp(self):
        super().setUp()
        for code, kind, statutory, amount in (("LWF", ComponentKind.DEDUCTION, Statutory.LWF, "3"),
                                              ("LWF-ER", ComponentKind.EMPLOYER_COST, Statutory.LWF_EMPLOYER, "15")):
            component = PayComponent.objects.create(
                code=code, name=code, kind=kind, basis=ComponentBasis.SLAB, liability_account=self.pt_payable,
                expense_account=self.employer if kind == ComponentKind.EMPLOYER_COST else None,
                sequence=97, statutory=statutory)
            for when in (6, 12):
                PayComponentSlab.objects.create(component=component, state="WB", month=when, amount=Decimal(amount))
            setattr(self, code.replace("-", "_").lower(), component)

    def test_L1_the_welfare_fund_is_taken_once_in_june_when_june_is_paid_in_two_runs(self):
        weaver = self.worker("W", work_state="WB")
        self.paid(weaver, self.basic, "9000")
        self.paid(weaver, self.lwf, "1")
        self.paid(weaver, self.lwf_er, "1")
        for first, last in ((D(2026, 6, 1), D(2026, 6, 15)), (D(2026, 6, 16), D(2026, 6, 30))):
            run = PayRun.objects.create(period_start=first, period_end=last, pay_date=last)
            run.calculate(employees=[weaver])
            run.post()
        rows = lwf_register(D(2026, 6, 1))
        self.assertEqual((sum(r["employee_share"] for r in rows), sum(r["employer_share"] for r in rows)),
                         (Decimal("3.00"), Decimal("15.00")))

    def test_L2_a_figure_entered_after_the_run_was_calculated_stops_its_post_and_one_dated_after_does_not(self):
        c = self.person("C", basic="20000")
        c.uan = "100000000077"
        c.save()
        overtime = PayComponent.objects.create(
            code="OTO", name="Overtime", kind=ComponentKind.EARNING, basis=ComponentBasis.OVERTIME_ORDINARY,
            expense_account=self.wages, sequence=45)
        self.basic.is_ordinary_wages = True
        self.basic.save()
        self.paid(c, overtime, "1")
        AttendanceDay.objects.create(employee=c, on=D(2026, 10, 8), status=AttendanceStatus.PRESENT,
                                     overtime_hours=Decimal("4"))
        run = self.month_run(2026, 10, post=False, employees=[c])
        StatutoryFigure.objects.create(kind=FigureKind.OVERTIME_MULTIPLIER, effective_from=D(2026, 11, 1),
                                       value=Decimal("2.5"))
        run.post()   # dated after the period: nothing moved, so the post goes through
        self.assertEqual(run.status, "posted")
        other = self.month_run(2026, 11, post=False, employees=[c])
        AttendanceDay.objects.create(employee=c, on=D(2026, 11, 9), status=AttendanceStatus.PRESENT,
                                     overtime_hours=Decimal("2"))
        self.assertTrue(refused(other.post), "register moved after calculation")

    def test_L3_a_posted_months_files_do_not_follow_a_row_entered_afterwards(self):
        for component, marked in ((self.pf, Statutory.PF), (self.pf_er, Statutory.PF_EMPLOYER)):
            component.base_ceiling, component.ceiling_figure = None, FigureKind.EPF_WAGE_CEILING
            component.statutory = marked
            component.save()
        c = self.person("C", basic="20000")
        c.uan = "100000000078"
        c.save()
        from .pay_details import EmployeePayDetails as Details
        Details.objects.create(employee=c, date_of_birth=D(1990, 1, 1))
        september = self.month_run(2026, 9, employees=[c])
        before = ecr_rows(september)
        StatutoryFigure.objects.create(kind=FigureKind.EPF_WAGE_CEILING, effective_from=D(2026, 9, 1),
                                       value=Decimal("25000"))
        StatutoryFigure.objects.create(kind=FigureKind.EPS_WAGE_CEILING, effective_from=D(2026, 9, 1),
                                       value=Decimal("25000"))
        self.assertEqual(ecr_rows(september), before)

    def test_L4_pt_for_a_state_the_person_is_not_in_is_refused_not_skipped(self):
        wb = PayComponent.objects.create(code="PTWB", name="PT", kind=ComponentKind.DEDUCTION,
                                         basis=ComponentBasis.SLAB, liability_account=self.pt_payable,
                                         sequence=95, statutory=Statutory.PT)
        wb.base_components.set([self.basic])
        PayComponentSlab.objects.create(component=wb, state="WB", above=Decimal("0"), amount=Decimal("110"))
        person = self.worker("K", work_state="Karnataka")
        self.paid(person, self.basic, "12000")
        self.paid(person, wb, "1")
        self.assertTrue(refused(self.month_run, 2026, 9, post=False, employees=[person]))

    def test_L5_hours_a_day_of_nought_is_refused_in_words(self):
        from .models import Employee
        person = self.worker("Z")
        person.hours_a_day = Decimal("0")
        self.assertTrue(refused(person.save),
                        "an employee can be saved with a working day of no hours; overtime would divide by it")


class WhoMayReadAndDoWhat(base.OutputsTestCase):
    """The roles over the API: bank details, PAN, date of birth, the bank file, payslips and who holds what."""

    def setUp(self):
        super().setUp()
        self.one, self.two = self.employee("E1"), self.employee("E2")
        for person, amount in ((self.one, "12000"), (self.two, "8450.50")):
            self.pay(person, self.salary, amount)
            self.banked(person)
            details = EmployeePayDetails.objects.get(employee=person)
            details.pan, details.date_of_birth = "ABCDE1234F", D(1990, 1, 1)
            details.save()
        self.posted = self.june()
        self.file = SalaryTransferFile.prepare(self.posted)
        self.slip_one = self.posted.payslips.get(employee=self.one)
        self.slip_two = self.posted.payslips.get(employee=self.two)
        self.advance = None

    def urls(self):
        return {
            "pay-details": "/api/hr/pay-details/",
            "pay-details one": f"/api/hr/pay-details/{EmployeePayDetails.objects.get(employee=self.one).pk}/",
            "statutory-figures": "/api/hr/statutory-figures/",
            "salary-files": "/api/hr/salary-files/",
            "salary-file csv": f"/api/hr/salary-files/{self.file.pk}/csv/",
            "salary-advances": "/api/hr/salary-advances/",
            "payslips": "/api/hr/payslips/",
            "payslip pdf": f"/api/hr/payslips/{self.slip_one.pk}/pdf/",
            "ecr": f"/api/hr/pay-runs/{self.posted.pk}/ecr/",
            "pt register": "/api/hr/statutory-registers/?kind=pt&month=2026-06",
            "history of pay details": f"/api/core/history/?model=hr.employeepaydetails&id="
                                      f"{EmployeePayDetails.objects.get(employee=self.one).pk}",
            "my-payslips": "/api/hr/my-payslips/",
        }

    def test_W1_who_reads_what(self):
        allowed = {
            "Payroll Officer": {"pay-details", "pay-details one", "statutory-figures", "salary-files", "salary-file csv",
                                "salary-advances", "payslips", "payslip pdf", "pt register", "history of pay details"},
            "Controller": {"pay-details", "pay-details one", "statutory-figures", "salary-files", "salary-file csv",
                           "salary-advances", "payslips", "payslip pdf", "pt register", "history of pay details"},
            "Line Manager": set(), "HR Admin": set(), "Sales Rep": set(), "Warehouse Staff": set(),
            "Employee Self Service": {"my-payslips"},
        }
        wrong = []
        for role, readable in allowed.items():
            client = self.as_role(role, self.one if role == "Employee Self Service" else None)
            for name, url in self.urls().items():
                if name == "ecr":
                    continue   # needs UANs; the door is hr.view_payslip, the same as the payslips'
                got = client.get(url).status_code
                if (got == 200) != (name in readable):
                    wrong.append((role, name, got))
        self.assertEqual(wrong, [])

    def test_W2_no_other_endpoint_a_role_reads_carries_the_bank_account_pan_or_birth_date(self):
        leaks = []
        for role in ("Line Manager", "HR Admin", "Payroll Officer", "Controller"):
            client = self.as_role(role)
            for url in ("/api/hr/employees/", f"/api/hr/employees/{self.one.pk}/", "/api/hr/payslips/",
                        f"/api/hr/pay-runs/{self.posted.pk}/", "/api/hr/compensation/",
                        f"/api/hr/payslips/{self.slip_one.pk}/", "/api/core/history/?model=hr.employee&id="
                        f"{self.one.pk}", "/api/hr/statutory-registers/?kind=pt&month=2026-06"):
                body = client.get(url).content
                for secret in (b"ABCDE1234F", b"1990-01-01", f"3001{self.one.pk:08d}".encode(), b"SBIN0001234"):
                    if secret in body:
                        leaks.append((role, url, secret))
        self.assertEqual(leaks, [])

    def test_W3_the_payslip_pdf_carries_no_bank_account_pan_or_birth_date(self):
        from .payslip_print import payslip_pdf

        pdf = payslip_pdf(self.slip_one)
        for secret in (b"ABCDE1234F", b"SBIN0001234", f"3001{self.one.pk:08d}".encode(), b"1990"):
            self.assertNotIn(secret, pdf)

    def test_W4_an_employee_reaches_only_their_own_payslip(self):
        mine, theirs = self.as_role("Employee Self Service", self.one), self.as_role("Employee Self Service", self.two)
        got = mine.get("/api/hr/my-payslips/").json()
        rows = got["results"] if isinstance(got, dict) else got
        self.assertEqual([row["id"] for row in rows], [self.slip_one.pk])
        # another's by id, by pdf, by a filter, by the payslips list
        self.assertEqual(mine.get(f"/api/hr/my-payslips/{self.slip_two.pk}/").status_code, 404)
        self.assertEqual(mine.get(f"/api/hr/my-payslips/{self.slip_two.pk}/pdf/").status_code, 404)
        filtered = mine.get(f"/api/hr/my-payslips/?employee={self.two.pk}&run={self.posted.pk}").json()
        rows = filtered["results"] if isinstance(filtered, dict) else filtered
        self.assertEqual([row["id"] for row in rows], [self.slip_one.pk])
        self.assertEqual(mine.get("/api/hr/payslips/").status_code, 403)
        self.assertEqual(mine.get(f"/api/hr/payslips/{self.slip_two.pk}/pdf/").status_code, 403)
        self.assertEqual(theirs.get(f"/api/hr/my-payslips/{self.slip_one.pk}/pdf/").status_code, 404)
        # a voided run's slip is nobody's to read
        self.posted.void(on_date=JULY1)
        rows = mine.get("/api/hr/my-payslips/").json()
        self.assertEqual(len(rows["results"] if isinstance(rows, dict) else rows), 0)
        self.assertEqual(mine.get(f"/api/hr/my-payslips/{self.slip_one.pk}/pdf/").status_code, 404)

    def test_W5_the_payroll_officer_cannot_pay_mark_paid_return_or_void_what_the_controller_holds(self):
        officer = self.as_role("Payroll Officer")
        paid = self.money(self.one.party, "20450.50")
        row = self.file.rows.first()
        refusals = {
            "mark_paid": officer.post(f"/api/hr/salary-files/{self.file.pk}/mark_paid/", {"payment": paid.pk}, format="json"),
            "return_transfer": officer.post(f"/api/hr/salary-files/{self.file.pk}/return_transfer/",
                                            {"row": row.pk, "receipt": paid.pk}, format="json"),
            "delete the file": officer.delete(f"/api/hr/salary-files/{self.file.pk}/"),
            "post a run": officer.post(f"/api/hr/pay-runs/{self.posted.pk}/post/"),
            "void a run": officer.post(f"/api/hr/pay-runs/{self.posted.pk}/void/", {}, format="json"),
            "pay a slip": officer.post(f"/api/hr/payslips/{self.slip_one.pk}/pay/", {"payment": paid.pk}, format="json"),
            "void a payment": officer.post(f"/api/accounting/payments/{paid.pk}/void/", {}, format="json"),
            "give an advance": officer.post("/api/hr/salary-advances/", {
                "employee": self.one.pk, "payment": paid.pk, "component": self.recovery.pk, "amount": "20450.50",
                "instalment": "100", "recover_from": "2026-07-01"}, format="json"),
        }
        self.assertEqual({name: r.status_code for name, r in refusals.items() if r.status_code != 403}, {})

    def test_W6_the_controller_cannot_write_pay_details_or_figures(self):
        controller = self.as_role("Controller")
        details = EmployeePayDetails.objects.get(employee=self.one)
        got = {
            "patch details": controller.patch(f"/api/hr/pay-details/{details.pk}/", {"account_number": "123456789012"},
                                              format="json").status_code,
            "post figure": controller.post("/api/hr/statutory-figures/", {
                "kind": "epf_ceiling", "effective_from": "2026-09-17", "value": "25000"}, format="json").status_code,
        }
        self.assertEqual(got, {"patch details": 403, "post figure": 403})

    def test_W7_a_working_day_of_no_hours_is_refused_over_the_api(self):
        hr = self.as_role("HR Admin")
        got = hr.patch(f"/api/hr/employees/{self.one.pk}/", {"hours_a_day": "0"}, format="json")
        self.assertEqual(got.status_code, 400, got.content)

    def test_W8_pay_details_change_is_in_the_history_without_the_numbers_and_only_payrolls(self):
        officer = self.as_role("Payroll Officer")
        details = EmployeePayDetails.objects.get(employee=self.one)
        done = officer.patch(f"/api/hr/pay-details/{details.pk}/", {"account_number": "555500001111"}, format="json")
        self.assertEqual(done.status_code, 200, done.content)
        body = officer.get(f"/api/core/history/?model=hr.employeepaydetails&id={details.pk}").content
        self.assertNotIn(b"555500001111", body)
        self.assertNotIn(b"3001", body)


class PensionPast58AndTheBooks(DatedFiguresTestCase):
    """O236/O237 confirmed by account, and the ECR/ledger tie for a member past 58."""

    def setUp(self):
        super().setUp()
        for component, marked in ((self.pf, Statutory.PF), (self.pf_er, Statutory.PF_EMPLOYER)):
            component.statutory = marked
            component.save()
        self.eps_payable = Account.objects.create(code="2340", name="EPS payable", account_type=AccountType.LIABILITY)
        self.eps = self.percent("EPS", 65, ComponentKind.EMPLOYER_COST, self.eps_payable, base=[self.basic, self.special],
                                ceiling="15000", rounding=self.pf.rounding)
        self.eps.statutory = Statutory.EPS
        self.eps.save()
        self.pf_er.basis = ComponentBasis.PERCENT_OF_GROSS
        self.member = self.worker("D", uan="100000000030")
        for component, rate in ((self.basic, "20000"), (self.pf, "12"), (self.pf_er, "3.67"), (self.eps, "8.33")):
            self.paid(self.member, component, rate)

    def credited(self, run, account):
        return sum((l.credit for l in JournalLine.objects.filter(entry=run.journal_entry, account=account)), Decimal("0"))

    def test_P1_the_ecr_and_the_ledger_agree_by_account_before_and_after_58(self):
        EmployeePayDetails.objects.create(employee=self.member, date_of_birth=D(1968, 10, 15))
        rows = []
        for number in (10, 11):
            run = self.month_run(2026, number, employees=[self.member])
            found = ecr_rows(run)[0]
            rows.append((number, found["eps"], self.credited(run, self.eps_payable),
                         found["epf_diff"], self.credited(run, self.pf_payable) - found["epf"]))
        # per month: EPS filed == EPS payable credited; EPF employer filed == PF payable credited less the member's own
        self.assertEqual([(n, filed, booked) for n, filed, booked, _a, _b in rows if filed != booked], [],
                         "EPS filed against EPS payable credited")
        self.assertEqual([(n, filed, booked) for n, _x, _y, filed, booked in rows if filed != booked], [],
                         "employer EPF filed against PF payable credited, less the member's share")

    def test_P2_the_employers_12_percent_is_1800_not_1801(self):
        EmployeePayDetails.objects.create(employee=self.member, date_of_birth=D(1990, 10, 15))
        run = self.month_run(2026, 10, employees=[self.member])
        found = ecr_rows(run)[0]
        self.assertEqual(found["eps"] + found["epf_diff"], 1800)


class TheLoginLinkAndTheOvertimeDay(base.OutputsTestCase):
    def test_W9_nobody_reads_another_employees_payslips_by_linking_their_login_to_that_employees_record(self):
        """An HR Admin who is also an employee relinks the managing director's record to their own login."""
        director, clerk = self.employee("MD"), self.employee("HR1")
        self.pay(director, self.salary, "90000")
        self.pay(clerk, self.salary, "20000")
        run = self.june()
        client = self.as_role("Employee Self Service", clerk)
        user = clerk.user
        user.groups.add(*__import__("django").contrib.auth.models.Group.objects.filter(name="HR Admin"))
        before = client.get("/api/hr/my-payslips/").json()
        first = client.patch(f"/api/hr/employees/{clerk.pk}/", {"user": None}, format="json")
        second = client.patch(f"/api/hr/employees/{director.pk}/", {"user": user.pk}, format="json")
        seen = client.get("/api/hr/my-payslips/").json()
        rows = seen["results"] if isinstance(seen, dict) else seen
        names = {row["employee"] for row in rows}
        self.assertNotIn("MD", names, (first.status_code, second.status_code, second.content[:200]))
        del run, before

    def test_W10_a_working_day_of_no_hours_does_not_crash_the_run(self):
        person = self.employee("OT1")
        # Adapted (fix_people2): save() refuses 0 now (L5, W7); a row from before the check, put in by update().
        type(person).objects.filter(pk=person.pk).update(hours_a_day=Decimal("0"))
        person.refresh_from_db()
        self.pay(person, self.salary, "13500")
        self.salary.is_ordinary_wages = True
        self.salary.save()
        overtime = PayComponent.objects.create(code="OTO", name="Overtime", kind=ComponentKind.EARNING,
                                               basis=ComponentBasis.OVERTIME_ORDINARY, expense_account=self.wages,
                                               sequence=45)
        self.pay(person, overtime, "1")
        AttendanceDay.objects.create(employee=person, on=D(2026, 6, 8), status=AttendanceStatus.PRESENT,
                                     overtime_hours=Decimal("2"))
        run = self.pay_run(*base.JUNE, pay_date=base.JUNE[1])
        try:
            run.calculate(employees=[person])
        except ValidationError:
            return
        # no refusal: then it must at least not have paid by dividing by nothing
        self.fail("calculated without a word")


class TheGoLiveRun(PensionPast58AndTheBooks):
    test_P1_the_ecr_and_the_ledger_agree_by_account_before_and_after_58 = None
    test_P2_the_employers_12_percent_is_1800_not_1801 = None

    def test_P3_every_member_without_a_date_of_birth_is_named_in_one_refusal(self):
        """Two more members on the pension component with no date of birth: the first refusal names all of them."""
        others = []
        for code in ("D2", "D3"):
            person = self.worker(code, uan=f"10000000004{code[1]}")
            for component, rate in ((self.basic, "20000"), (self.pf, "12"), (self.pf_er, "3.67"), (self.eps, "8.33")):
                self.paid(person, component, rate)
            others.append(person)
        try:
            self.month_run(2026, 10, post=False, employees=[self.member, *others])
        except ValidationError as exc:
            said = " ".join(exc.messages)
            self.assertTrue(all(code in said for code in ("D2", "D3", " D ")) or ("D, D2, D3" in said), said)
        else:
            self.fail("calculated without a date of birth")
