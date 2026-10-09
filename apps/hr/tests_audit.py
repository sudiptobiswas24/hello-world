"""
Audit pass over HR.

`manage.py audit_invariants` was clean, so this is the other half: a
probe that set up real documents and asserted claims about money and
state. Twelve checks, four defects, none of which a suite of 1,494
passing tests had noticed.

One test per finding, named after the fact it protects.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import (
    Account,
    AccountType,
    JournalLine,
    Payment,
    PaymentDirection,
)
from apps.core.models import Company, Currency, Party, PartyRole, PartyRoleAssignment

from .models import (
    ComponentBasis,
    ComponentKind,
    Employee,
    EmployeeCompensation,
    EmploymentStatus,
    LeavePolicy,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    PayComponent,
    PayRun,
    Timesheet,
    TimesheetEntry,
    leave_taken,
)


class AuditTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        acc = lambda c, n, t, **more: Account.objects.create(code=c, name=n, account_type=t, **more)
        self.wages = acc("6000", "Wages", AccountType.EXPENSE)
        self.tax_payable = acc("2200", "Tax payable", AccountType.LIABILITY)
        self.net_pay = acc("2300", "Net pay payable", AccountType.LIABILITY)
        self.bank = acc("1010", "Bank", AccountType.ASSET, holds_money=True)
        Company.objects.create(
            name="Test Co", base_currency=self.usd, net_pay_account=self.net_pay
        )
        self.salary = PayComponent.objects.create(
            code="SAL", name="Salary", kind=ComponentKind.EARNING,
            basis=ComponentBasis.FIXED, expense_account=self.wages, sequence=10,
        )
        self.tax = PayComponent.objects.create(
            code="TAX", name="Tax", kind=ComponentKind.DEDUCTION,
            basis=ComponentBasis.PERCENT_OF_GROSS,
            liability_account=self.tax_payable, sequence=50,
        )
        self.policy = LeavePolicy.objects.create(
            code="HOL", name="Holiday", leave_type="vacation", annual_days=Decimal("25")
        )
        self.boss = self.employee("MGR")

    def employee(self, code, hire=datetime.date(2020, 1, 1), **kwargs):
        party = Party.objects.create(code=f"P-{code}", name=code)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        # Everyone after the boss reports to them, unless a test says
        # otherwise: only a manager decides leave.
        if getattr(self, "boss", None) is not None:
            kwargs.setdefault("manager", self.boss)
        return Employee.objects.create(
            party=party, employee_number=code, hire_date=hire, **kwargs
        )

    def balance(self, account):
        return sum(
            (l.debit - l.credit for l in JournalLine.objects.filter(account=account)),
            Decimal("0"),
        )

    def payroll(self, person, gross="5000"):
        EmployeeCompensation.objects.create(
            employee=person, component=self.salary, amount=Decimal(gross),
            effective_from=datetime.date(2020, 1, 1),
        )
        EmployeeCompensation.objects.create(
            employee=person, component=self.tax, amount=Decimal("20"),
            effective_from=datetime.date(2020, 1, 1),
        )
        run = PayRun.objects.create(
            period_start=datetime.date(2026, 6, 1), period_end=datetime.date(2026, 6, 30),
            pay_date=datetime.date(2026, 6, 30),
        )
        run.calculate(employees=[person])
        run.post()
        return run

    def settle(self, person, slip):
        payment = Payment.objects.create(
            party=person.party, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 6, 30), amount=slip.net(),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.net_pay,
        )
        payment.post()
        slip.pay(payment)
        return payment


class AVoidedPaymentOwesTheMoneyAgainTests(AuditTestCase):
    """
    Voiding a payment credits net pay payable straight back, so the
    wages are owed again — but the payslip went on pointing at the
    voided payment and reading as paid. unpaid_net() said zero while
    the control account said four thousand.
    """

    def test_the_slip_is_unpaid_again(self):
        person = self.employee("V1")
        run = self.payroll(person)
        slip = run.payslips.get()
        payment = self.settle(person, slip)
        self.assertTrue(slip.is_paid())
        payment.void()
        self.assertFalse(slip.is_paid())

    def test_the_run_knows_it_owes_the_money_again(self):
        person = self.employee("V2")
        run = self.payroll(person)
        slip = run.payslips.get()
        payment = self.settle(person, slip)
        self.assertEqual(run.unpaid_net(), Decimal("0"))
        payment.void()
        self.assertEqual(run.unpaid_net(), Decimal("4000.00"))

    def test_the_slip_agrees_with_the_control_account(self):
        person = self.employee("V3")
        run = self.payroll(person)
        slip = run.payslips.get()
        payment = self.settle(person, slip)
        payment.void()
        self.assertEqual(self.balance(self.net_pay), Decimal("-4000.00"))
        self.assertEqual(run.unpaid_net(), Decimal("4000.00"))

    def test_the_slip_can_be_paid_again(self):
        person = self.employee("V4")
        run = self.payroll(person)
        slip = run.payslips.get()
        self.settle(person, slip).void()
        self.settle(person, slip)
        self.assertEqual(self.balance(self.net_pay), Decimal("0.00"))

    def test_a_voided_payment_settles_nothing(self):
        person = self.employee("V5")
        run = self.payroll(person)
        slip = run.payslips.get()
        payment = Payment.objects.create(
            party=person.party, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 6, 30), amount=slip.net(),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.net_pay,
        )
        payment.post()
        payment.void()
        with self.assertRaises(ValidationError) as caught:
            slip.pay(payment)
        self.assertIn("settles nothing", str(caught.exception))


class OnlyWagesPaidSettleAPayslipTests(AuditTestCase):
    """
    `pay()` asked only whether the amount matched. A draft payment, money
    received, a payment to somebody else, one booked against payables,
    and one payment for two people's wages all marked the slip paid,
    while net pay payable went on saying the wages were owed. The slip
    and the control account must be asked the same question.
    """

    def payment(self, person, slip, **overrides):
        fields = {
            "party": person.party, "direction": PaymentDirection.DISBURSEMENT,
            "payment_date": datetime.date(2026, 6, 30), "amount": slip.net(),
            "currency": self.usd, "bank_account": self.bank,
            "counterpart_account": self.net_pay,
        }
        fields.update(overrides)
        post = fields.pop("post", True)
        payment = Payment.objects.create(**fields)
        if post:
            payment.post()
        return payment

    def refused(self, code, message, **overrides):
        person = self.employee(code)
        slip = self.payroll(person).payslips.get()
        with self.assertRaisesMessage(ValidationError, message):
            slip.pay(self.payment(person, slip, **overrides))
        self.assertFalse(slip.is_paid())

    def test_a_draft_payment_settles_nothing(self):
        self.refused("W1", "not been posted", post=False)

    def test_money_received_does_not_pay_wages(self):
        self.refused("W2", "money paid out", direction=PaymentDirection.RECEIPT)

    def test_a_payment_against_another_account_does_not_clear_net_pay(self):
        payables = Account.objects.create(code="2100", name="AP",
                                          account_type=AccountType.LIABILITY)
        self.refused("W3", "net pay payable", counterpart_account=payables)

    def test_a_payment_to_somebody_else_does_not_pay_this_person(self):
        other = self.employee("W4X")
        self.refused("W4", "was made to", party=other.party)

    def test_one_payment_does_not_pay_two_slips(self):
        person = self.employee("W5")
        june = self.payroll(person).payslips.get()
        july = PayRun.objects.create(
            period_start=datetime.date(2026, 7, 1), period_end=datetime.date(2026, 7, 31),
            pay_date=datetime.date(2026, 7, 31),
        )
        july.calculate(employees=[person])
        july.post()
        july = july.payslips.get()
        payment = self.payment(person, june)
        june.pay(payment)
        with self.assertRaisesMessage(ValidationError, "already settles"):
            july.pay(payment)
        self.assertEqual((self.balance(self.net_pay), july.is_paid()),
                         (Decimal("-4000.00"), False))


class APayrollThatPaysNothingTests(AuditTestCase):
    """
    A run with payslips but no lines posted an entry with no lines,
    which balances trivially and records nothing — against a run that
    looked as though payroll had been done.
    """

    def test_it_is_refused(self):
        person = self.employee("Z1")
        run = PayRun.objects.create(
            period_start=datetime.date(2026, 6, 1), period_end=datetime.date(2026, 6, 30),
            pay_date=datetime.date(2026, 6, 30),
        )
        run.calculate(employees=[person])
        with self.assertRaises(ValidationError) as caught:
            run.post()
        self.assertIn("pays nothing", str(caught.exception))


class LeaveDoesNotCrossAYearEndTests(AuditTestCase):
    """
    A holiday from 28 December to 8 January charged all nine days to the
    year it started in, overspending one allowance and leaving the other
    untouched. An allowance is annual, and a single frozen total cannot
    be split afterwards.
    """

    def test_it_is_refused(self):
        person = self.employee("Y1")
        with self.assertRaises(ValidationError) as caught:
            LeaveRequest.objects.create(
                employee=person, policy=self.policy, leave_type=LeaveType.VACATION,
                start_date=datetime.date(2026, 12, 28),
                end_date=datetime.date(2027, 1, 8),
            )
        self.assertIn("crosses a year end", str(caught.exception))

    def test_each_half_lands_in_its_own_year(self):
        person = self.employee("Y2")
        for start, end in (
            (datetime.date(2026, 12, 28), datetime.date(2026, 12, 31)),
            (datetime.date(2027, 1, 1), datetime.date(2027, 1, 8)),
        ):
            booking = LeaveRequest.objects.create(
                employee=person, policy=self.policy, leave_type=LeaveType.VACATION,
                start_date=start, end_date=end,
            )
            booking.approve(by=self.boss)
        # Mon to Thu is four; the Friday plus the following week is six.
        self.assertEqual(leave_taken(person, self.policy, 2026), Decimal("4.00"))
        self.assertEqual(leave_taken(person, self.policy, 2027), Decimal("6.00"))


class TerminationDealsWithWhatComesAfterTests(AuditTestCase):
    """
    Approved leave in March survived a December leaving date, and eight
    hours claimed on the fourth survived a leaving date of the second.
    Both records look entirely reasonable on their own, and the hours
    would have been paid.
    """

    def booked(self, person, start, end):
        booking = LeaveRequest.objects.create(
            employee=person, policy=self.policy, leave_type=LeaveType.VACATION,
            start_date=start, end_date=end,
        )
        booking.approve(by=self.boss)
        return booking

    def test_a_leaving_date_before_approved_leave_is_refused(self):
        person = self.employee("T1")
        self.booked(person, datetime.date(2027, 3, 1), datetime.date(2027, 3, 5))
        person.termination_date = datetime.date(2026, 12, 31)
        person.employment_status = EmploymentStatus.TERMINATED
        with self.assertRaises(ValidationError) as caught:
            person.save()
        self.assertIn("approved leave to", str(caught.exception))

    def test_terminate_can_cancel_it_and_says_so(self):
        person = self.employee("T2")
        booking = self.booked(person, datetime.date(2027, 3, 1), datetime.date(2027, 3, 5))
        cancelled, _removed = person.terminate(
            datetime.date(2026, 12, 31), cancel_future=True
        )
        self.assertEqual([r.pk for r in cancelled], [booking.pk])
        booking.refresh_from_db()
        self.assertEqual(booking.status, LeaveStatus.CANCELLED)
        self.assertEqual(person.employment_status, EmploymentStatus.TERMINATED)

    def test_leave_spanning_the_last_day_is_not_split_for_them(self):
        person = self.employee("T3")
        # A holiday running 28 to 31 December, with a leaving date of the
        # 30th inside it. Splitting somebody's holiday across their last
        # day is a decision, not one to make for them.
        self.booked(person, datetime.date(2026, 12, 28), datetime.date(2026, 12, 31))
        with self.assertRaises(ValidationError) as caught:
            person.terminate(datetime.date(2026, 12, 30), cancel_future=True)
        self.assertIn("spans", str(caught.exception))

    def test_a_leaving_date_before_claimed_time_is_refused(self):
        person = self.employee("T4")
        sheet = Timesheet.objects.create(
            employee=person, period_start=datetime.date(2026, 6, 1),
            period_end=datetime.date(2026, 6, 5),
        )
        TimesheetEntry.objects.create(
            timesheet=sheet, date=datetime.date(2026, 6, 4), hours=Decimal("8")
        )
        person.termination_date = datetime.date(2026, 6, 2)
        person.employment_status = EmploymentStatus.TERMINATED
        with self.assertRaises(ValidationError) as caught:
            person.save()
        self.assertIn("time claimed on", str(caught.exception))

    def test_terminate_removes_time_that_was_never_worked(self):
        person = self.employee("T5")
        sheet = Timesheet.objects.create(
            employee=person, period_start=datetime.date(2026, 6, 1),
            period_end=datetime.date(2026, 6, 5),
        )
        entry = TimesheetEntry.objects.create(
            timesheet=sheet, date=datetime.date(2026, 6, 4), hours=Decimal("8")
        )
        person.terminate(datetime.date(2026, 6, 2), cancel_future=True)
        self.assertFalse(TimesheetEntry.objects.filter(pk=entry.pk).exists())

    def test_time_before_the_leaving_date_is_left_alone(self):
        person = self.employee("T6")
        sheet = Timesheet.objects.create(
            employee=person, period_start=datetime.date(2026, 6, 1),
            period_end=datetime.date(2026, 6, 5),
        )
        kept = TimesheetEntry.objects.create(
            timesheet=sheet, date=datetime.date(2026, 6, 1), hours=Decimal("8")
        )
        person.terminate(datetime.date(2026, 6, 2), cancel_future=True)
        self.assertTrue(TimesheetEntry.objects.filter(pk=kept.pk).exists())

    def test_terminating_somebody_with_nothing_outstanding_just_works(self):
        person = self.employee("T7")
        person.terminate(datetime.date(2026, 6, 30))
        person.refresh_from_db()
        self.assertEqual(person.termination_date, datetime.date(2026, 6, 30))
        self.assertEqual(person.employment_status, EmploymentStatus.TERMINATED)

    def test_a_leaving_date_inside_a_posted_run_is_refused(self):
        # June paid all 22 days; left on the 15th, the slip would read 11.
        person = self.employee("P1")
        run = self.payroll(person)
        with self.assertRaisesMessage(ValidationError, "void the run to change their leaving date"):
            person.terminate(datetime.date(2026, 6, 15))
        person.refresh_from_db()
        self.assertEqual((person.termination_date, run.payslips.get().days_employed()), (None, Decimal("22")))

    def test_a_start_date_moved_into_a_posted_run_is_refused(self):
        person = self.employee("P2")
        self.payroll(person)
        person.hire_date = datetime.date(2026, 6, 16)
        with self.assertRaisesMessage(ValidationError, "void the run to change their start date"):
            person.save()

    def test_a_leaving_date_a_posted_run_paid_to_is_not_taken_away(self):
        # Paid to 15 June; with the date gone the slip would read the whole month.
        person = self.employee("P3")
        person.terminate(datetime.date(2026, 6, 15))
        self.payroll(person)
        person.termination_date, person.employment_status = None, EmploymentStatus.ACTIVE
        with self.assertRaisesMessage(ValidationError, "void the run to change their leaving date"):
            person.save()

    def test_leaving_on_or_after_the_last_day_a_run_paid_is_accepted(self):
        person = self.employee("P4")
        self.payroll(person)
        person.terminate(datetime.date(2026, 6, 30))
        person.terminate(datetime.date(2026, 7, 10))
        person.refresh_from_db()
        self.assertEqual(person.termination_date, datetime.date(2026, 7, 10))

    def test_hr_is_refused_a_leaving_date_inside_a_posted_run_over_the_api(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("hr-admin")
        user.groups.add(Group.objects.get(name="HR Admin"))
        client = APIClient()
        client.force_authenticate(user)
        person = self.employee("P5")
        self.payroll(person)
        refused = client.patch(f"/api/hr/employees/{person.pk}/", {
            "termination_date": "2026-06-15", "employment_status": "terminated"}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("void the run to change their leaving date", refused.content.decode())

    def test_re_saving_an_unchanged_leaving_date_does_not_re_check(self):
        # Otherwise any later edit to a leaver's record would fail on
        # history that was already dealt with.
        person = self.employee("T8")
        person.terminate(datetime.date(2026, 6, 30))
        person.job_title = "Former engineer"
        person.save()
        self.assertEqual(person.job_title, "Former engineer")


class TheAuditAsksTests(TestCase):
    """The two shapes payroll found, asked of the code by `manage.py audit_invariants`."""

    def reversals(self, text):
        from pathlib import Path

        from apps.core.management.commands.audit_invariants import Command

        # Only the planted file is given, so every exemption of hr's reads as stale: not asked here.
        return [finding for finding in Command().corrections_dated_without_the_rule(
            ["hr"], {"hr": {Path("apps/hr/example.py"): text}}) if finding[0] != "stale exemption"]

    def test_a_reversal_dated_by_its_caller_is_reported_until_it_asks_the_rule(self):
        unasked = ("class Slip:\n    def void(self, on_date=None):\n"
                   "        self.entry.create_reversal(entry_date=on_date)\n")
        shared = ("class Slip:\n    def void(self, on_date=None):\n"
                  "        on_date = correction_date(on_date, self.day, 'Not voided on', 'it was paid')\n"
                  "        self.entry.create_reversal(entry_date=on_date)\n")
        self.assertEqual([shape for shape, _detail in self.reversals(unasked)], ["correction dated anywhere"])
        self.assertEqual(self.reversals(shared), [])

    def test_a_posted_pay_run_that_could_be_deleted_is_reported(self):
        from pathlib import Path

        from apps.core.management.commands.audit_invariants import Command, source_of

        def deletable(sources):
            return [detail.split(" ")[0] for shape, detail in Command().mutable_posted_documents(["hr"], {"hr": sources})
                    if shape == "deletable posted document"]

        sources = source_of("hr")
        self.assertEqual(deletable(sources), [])
        payroll = Path("apps/hr/payroll.py")
        sources[payroll] = sources[payroll].replace(
            "    def delete(self, *args, **kwargs):\n        # Posted, it is in the ledger",
            "    def kept(self, *args, **kwargs):\n        # Posted, it is in the ledger")
        self.assertEqual(deletable(sources), ["hr.PayRun"])

    def test_a_pay_run_posted_as_it_was_calculated_is_reported(self):
        from pathlib import Path

        from apps.core.management.commands.audit_invariants import Command, source_of

        sources = source_of("hr")
        self.assertEqual(Command().posted_as_calculated(["hr"], {"hr": sources}), [])
        payroll = Path("apps/hr/payroll.py")
        sources[payroll] = sources[payroll].replace("            slip.check_current()\n", "            pass\n")
        self.assertEqual([detail.split(" ")[0] for _shape, detail in
                          Command().posted_as_calculated(["hr"], {"hr": sources})], ["hr.PayRun.post"])
