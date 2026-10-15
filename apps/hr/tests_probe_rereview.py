"""
rereview (gate g21): the payroll review fixes (2852756 .. 257c44e) asked what they broke or left.
Each test states a fact about money; a failure is a finding.
Run: PYTHONPATH=scripts/gate .venv/bin/python manage.py test apps.hr.tests_probe_rereview --settings=fastsettings
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, PaymentDirection

from .advances import SalaryAdvance
from .bank_file import SalaryTransferFile
from .payroll import ComponentBasis, ComponentKind, PayComponent, PayRun, PayslipLine
from .statutory_files import lwf_register, pt_register
from . import tests_dated_figures, tests_pay_outputs
from .tests_pay_outputs import D, OutputsTestCase

ZERO = Decimal("0")


class TheAdvancesAfterTheReview(OutputsTestCase):
    def setUp(self):
        super().setUp()
        self.person = self.employee("A1")
        self.pay(self.person, self.salary, "12000")

    give = tests_pay_outputs.AdvancesTests.give
    month = tests_pay_outputs.AdvancesTests.month
    recovered_on = tests_pay_outputs.AdvancesTests.recovered_on

    def run_between(self, first, last, post=True):
        run = self.pay_run(first, last, pay_date=last)
        run.calculate()
        if post:
            run.post()
        return run

    def foots(self, *advances, account=None):
        account = account or self.advances
        return self.balance(account), sum((a.balance() for a in advances), Decimal("0.00"))

    def test_A1_a_leavers_final_run_after_a_regular_run_of_the_month_still_recovers_the_advance(self):
        advance = self.give()
        self.run_between(D(2026, 6, 1), D(2026, 6, 15))  # takes the month's 2,000
        self.person.termination_date, self.person.employment_status = D(2026, 6, 20), "terminated"
        self.person.save()
        final = self.run_between(D(2026, 6, 16), D(2026, 6, 30))
        taken = self.recovered_on(final)
        print(f"\nA1 final run took {taken}; advance balance {advance.balance()}; ledger {self.balance(self.advances)}")
        self.assertTrue(taken, "the final run took nothing of an advance with 3,000 left")
        ledger, derived = self.foots(advance)
        self.assertEqual(ledger, derived)
        self.assertLess(derived, Decimal("3000.00"))

    def test_A2_a_run_voided_after_the_later_run_of_its_month_and_run_again_foots_at_every_step(self):
        advance = self.give()
        first = self.run_between(D(2026, 6, 1), D(2026, 6, 15))
        second = self.run_between(D(2026, 6, 16), D(2026, 6, 30))
        self.assertEqual([self.recovered_on(first), self.recovered_on(second)], [[Decimal("2000.00")], []])
        self.assertEqual(self.foots(advance)[0], self.foots(advance)[1])
        first.void(on_date=D(2026, 7, 1))
        self.assertEqual(self.foots(advance), (Decimal("5000.00"), Decimal("5000.00")))
        again = self.run_between(D(2026, 6, 1), D(2026, 6, 15))
        self.assertEqual(self.recovered_on(again), [Decimal("2000.00")])
        july = self.run_between(D(2026, 7, 1), D(2026, 7, 31))
        self.assertEqual(self.recovered_on(july), [Decimal("2000.00")])
        self.assertEqual(self.foots(advance)[0], self.foots(advance)[1])
        self.assertEqual(self.foots(advance)[1], Decimal("1000.00"))

    def test_A3_a_run_voided_and_posted_again_recovers_the_same_instalment(self):
        advance = self.give()
        june = self.month(6)
        june.void(on_date=D(2026, 7, 1))
        again = self.month(6)
        self.assertEqual(self.recovered_on(again), [Decimal("2000.00")])
        self.assertEqual(self.foots(advance), (Decimal("3000.00"), Decimal("3000.00")))

    def test_A4_the_component_re_pointed_and_a_payment_voided_and_made_again_each_foot_to_its_own_account(self):
        first = self.give()  # on 1300
        other = Account.objects.create(code="1301", name="Employee advances II", account_type=AccountType.ASSET)
        PayComponent.objects.filter(pk=self.recovery.pk).update(liability_account=other)
        self.recovery.refresh_from_db()
        second = self.give(amount="3000", instalment="1000", counterpart=other)  # on 1301
        self.month(6)
        self.assertEqual(self.foots(first), (Decimal("3000.00"), Decimal("3000.00")))
        self.assertEqual(self.foots(second, account=other), (Decimal("2000.00"), Decimal("2000.00")))
        # June voided, the second advance's payment voided on its own, made again, given again.
        june = PayRun.objects.get(status="posted")
        june.void(on_date=D(2026, 7, 1))
        second.void(on_date=D(2026, 7, 1))
        third = self.give(amount="3000", instalment="1000", counterpart=other)
        self.month(6)
        self.assertEqual(self.foots(first), (Decimal("3000.00"), Decimal("3000.00")))
        self.assertEqual(self.foots(third, account=other), (Decimal("2000.00"), Decimal("2000.00")))
        self.assertEqual(second.balance(), ZERO)

    def test_A5_the_bank_an_advance_was_paid_through_cannot_be_unmarked_as_money_afterwards(self):
        self.give()
        self.bank.holds_money = False
        with self.assertRaises(ValidationError):
            self.bank.save()


class TheMonthsFiguresAfterTheReview(tests_dated_figures.TheMonthsFiguresAreOwedOnceTests):
    # The parent's own tests are not repeated here.
    test_two_runs_in_june_take_the_months_figures_once = None
    test_a_run_voided_between_them_reopens_the_month = None
    test_the_earlier_run_voided_after_the_later_one_leaves_the_month_to_the_next_run_of_it = None
    test_a_leavers_final_run_in_the_month_takes_only_what_is_left = None

    def register_total(self, month=D(2026, 6, 1)):
        lwf = lwf_register(month)
        return (sum((r["employee_share"] + r["employer_share"] for r in lwf), ZERO),
                sum((r["tax"] for r in pt_register(month)), ZERO))

    def ledger(self):
        return abs(self.balance(self.pt_payable))

    def test_M1_the_register_foots_to_the_ledger_after_every_void_and_run_again(self):
        steps = []
        first, second = self.fortnight(1), self.fortnight(16)
        steps.append(("both posted", self.register_total(), self.ledger()))
        first.void(on_date=D(2026, 7, 1))
        steps.append(("first voided", self.register_total(), self.ledger()))
        first = self.fortnight(1)
        steps.append(("first again", self.register_total(), self.ledger()))
        second.void(on_date=D(2026, 7, 1))
        steps.append(("second voided", self.register_total(), self.ledger()))
        second = self.fortnight(16)
        steps.append(("second again", self.register_total(), self.ledger()))
        for label, (lwf, pt), ledger in steps:
            print(f"M1 {label}: register LWF {lwf} PT {pt}; ledger {ledger}")
            self.assertEqual(lwf + pt, ledger, label)
        self.assertEqual(steps[0][1], (Decimal("18.00"), Decimal("110.00")))
        self.assertEqual(steps[-1][1], (Decimal("18.00"), Decimal("110.00")))

    def test_M2_the_month_is_the_periods_last_day_not_the_pay_date(self):
        """A June run paid on 5 July is June's for the welfare fund; July's run is July's."""
        run = PayRun.objects.create(period_start=D(2026, 6, 1), period_end=D(2026, 6, 30), pay_date=D(2026, 7, 5))
        run.calculate(employees=[self.weaver])
        run.post()
        july = PayRun.objects.create(period_start=D(2026, 7, 1), period_end=D(2026, 7, 15), pay_date=D(2026, 7, 20))
        july.calculate(employees=[self.weaver])
        july.post()
        self.assertEqual(self.register_total(D(2026, 6, 1))[0], Decimal("18.00"))
        self.assertEqual(self.register_total(D(2026, 7, 1))[0], ZERO)
        self.assertEqual(self.taken(july).get("LWF"), None)

    def test_M3_a_run_that_spans_the_month_end_and_the_run_before_it_do_not_take_the_fund_twice(self):
        """1-24 June, then 25 June-10 July: the first is June's, the second July's (a non-fund month)."""
        a = PayRun.objects.create(period_start=D(2026, 6, 1), period_end=D(2026, 6, 24), pay_date=D(2026, 6, 24))
        a.calculate(employees=[self.weaver])
        a.post()
        b = PayRun.objects.create(period_start=D(2026, 6, 25), period_end=D(2026, 7, 10), pay_date=D(2026, 7, 10))
        b.calculate(employees=[self.weaver])
        b.post()
        self.assertEqual(self.register_total(D(2026, 6, 1))[0], Decimal("18.00"))
        self.assertEqual(self.register_total(D(2026, 7, 1))[0], ZERO)

    def test_M4_a_draft_run_calculated_before_another_of_the_month_posted_is_refused_not_double_taking(self):
        first, second = self.fortnight(1, post=False), self.fortnight(16, post=False)
        first.post()
        with self.assertRaises(ValidationError):
            second.post()
        second.calculate(employees=[self.weaver])
        second.post()
        self.assertEqual(self.register_total()[0], Decimal("18.00"))


class TheBankFileAfterTheReview(OutputsTestCase):
    def setUp(self):
        super().setUp()
        self.people = [self.employee(code) for code in ("E1", "E2", "E3")]
        for person, amount in zip(self.people, ("12000", "8450.50", "3375")):
            self.pay(person, self.salary, amount)
        self.paid_run = self.june()
        for person in self.people:
            self.banked(person)

    def owed(self):
        return abs(self.balance(self.net_pay)), self.paid_run.unpaid_net()

    def test_B1_a_returned_transfer_is_repaid_by_a_second_file_and_the_books_foot(self):
        first = SalaryTransferFile.prepare(self.paid_run)
        paid = self.money(self.people[0].party, "23825.50")
        first.mark_paid(paid)
        row = first.rows.get(payslip__employee=self.people[1])
        receipt = self.money(self.people[1].party, "8450.50", direction=PaymentDirection.RECEIPT)
        row.return_transfer(receipt)
        self.assertEqual(self.owed(), (Decimal("8450.50"), Decimal("8450.50")))
        second = SalaryTransferFile.prepare(self.paid_run)
        self.assertEqual([r.payslip.employee.employee_number for r in second.rows.all()], ["E2"])
        repay = self.money(self.people[0].party, "8450.50")
        second.mark_paid(repay)
        self.assertEqual(self.owed(), (Decimal("0.00"), Decimal("0")))
        # the receipt cannot be voided under the second payment; the second payment can go; then the receipt
        with self.assertRaises(ValidationError):
            receipt.void(on_date=D(2026, 7, 1))
        repay.void(on_date=D(2026, 7, 1))
        self.assertEqual(self.owed(), (Decimal("8450.50"), Decimal("8450.50")))
        receipt.void(on_date=D(2026, 7, 1))
        self.assertEqual(self.owed(), (Decimal("0.00"), Decimal("0")))

    def test_B2_a_returned_transfer_repaid_by_a_payment_of_its_own_then_returned_again(self):
        first = SalaryTransferFile.prepare(self.paid_run)
        first.mark_paid(self.money(self.people[0].party, "23825.50"))
        row = first.rows.get(payslip__employee=self.people[1])
        row.return_transfer(self.money(self.people[1].party, "8450.50", direction=PaymentDirection.RECEIPT))
        slip = self.paid_run.payslips.get(employee=self.people[1])
        slip.pay(self.money(self.people[1].party, "8450.50"))
        self.assertEqual(self.owed(), (Decimal("0.00"), Decimal("0")))
        # the re-pay is itself voided: E2 is owed again, and the file's row is still the returned one.
        slip.payment.void(on_date=D(2026, 7, 1))
        self.assertEqual(self.owed(), (Decimal("8450.50"), Decimal("8450.50")))
        slip.refresh_from_db()
        slip.pay(self.money(self.people[1].party, "8450.50"))
        self.assertEqual(self.owed(), (Decimal("0.00"), Decimal("0")))
