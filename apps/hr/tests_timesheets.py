"""
Hours worked, and the payslip they reach.

Payroll could pay by the hour and had no idea how many anybody did — the
hours were handed to calculate() from outside, which means they came
from a spreadsheet, which means nothing in this system could say where a
number on a payslip came from.

A timesheet is an approved claim about time, the same shape as a leave
request, so it carries the same guards: no editing once approved, a way
back from an approval, and nobody signs off their own.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, AccountType
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
)

from .models import (
    ComponentBasis,
    ComponentKind,
    Employee,
    EmployeeCompensation,
    LeavePolicy,
    LeaveRequest,
    LeaveType,
    PayComponent,
    PayRun,
    Timesheet,
    TimesheetEntry,
    TimesheetStatus,
    approved_hours,
    hours_by_account,
)


class TimesheetTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.wages = acc("6000", "Wages", AccountType.EXPENSE)
        self.net_pay = acc("2300", "Net pay payable", AccountType.LIABILITY)
        self.project_a = acc("7100", "Project A", AccountType.EXPENSE)
        self.project_b = acc("7200", "Project B", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd, net_pay_account=self.net_pay
        )
        self.person = self.employee("E1")
        self.boss = self.employee("MGR")

    def employee(self, code, hire=datetime.date(2020, 1, 1), **kwargs):
        party = Party.objects.create(code=f"P-{code}", name=code)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        return Employee.objects.create(
            party=party, employee_number=code, hire_date=hire, **kwargs
        )

    def sheet(self, person=None, start=datetime.date(2026, 6, 1),
              end=datetime.date(2026, 6, 5)):
        return Timesheet.objects.create(
            employee=person or self.person, period_start=start, period_end=end
        )

    def entry(self, sheet, day, hours="8", **kwargs):
        return TimesheetEntry.objects.create(
            timesheet=sheet, date=day, hours=Decimal(hours), **kwargs
        )

    def filled(self, person=None, hours="8"):
        sheet = self.sheet(person)
        for day in range(1, 6):
            self.entry(sheet, datetime.date(2026, 6, day), hours)
        return sheet


class RecordingTimeTests(TimesheetTestCase):
    def test_a_sheet_totals_its_entries(self):
        sheet = self.filled()
        self.assertEqual(sheet.hours(), Decimal("40.00"))

    def test_billable_time_is_counted_apart(self):
        sheet = self.sheet()
        self.entry(sheet, datetime.date(2026, 6, 1), "6", is_billable=True)
        self.entry(sheet, datetime.date(2026, 6, 2), "2")
        self.assertEqual(sheet.hours(), Decimal("8.00"))
        self.assertEqual(sheet.billable_hours(), Decimal("6.00"))

    def test_an_entry_must_fall_inside_the_period(self):
        sheet = self.sheet()
        with self.assertRaises(ValidationError) as caught:
            self.entry(sheet, datetime.date(2026, 6, 9))
        self.assertIn("outside this timesheet's period", str(caught.exception))

    def test_a_day_cannot_hold_more_than_a_day(self):
        # Not about labour law, which differs everywhere — about catching
        # the typed 80 that was meant to be 8.
        sheet = self.sheet()
        with self.assertRaises(ValidationError):
            self.entry(sheet, datetime.date(2026, 6, 1), "80")

    def test_several_entries_on_one_day_are_added_up_against_the_cap(self):
        sheet = self.sheet()
        self.entry(sheet, datetime.date(2026, 6, 1), "20")
        with self.assertRaises(ValidationError) as caught:
            self.entry(sheet, datetime.date(2026, 6, 1), "6")
        self.assertIn("A day has", str(caught.exception))

    def test_hours_must_be_positive(self):
        from django.db.utils import IntegrityError

        sheet = self.sheet()
        with self.assertRaises(IntegrityError):
            TimesheetEntry.objects.create(
                timesheet=sheet, date=datetime.date(2026, 6, 1), hours=Decimal("-2")
            )

    def test_time_cannot_be_claimed_before_somebody_joined(self):
        newcomer = self.employee("N1", hire=datetime.date(2026, 6, 3))
        sheet = self.sheet(newcomer)
        with self.assertRaises(ValidationError) as caught:
            self.entry(sheet, datetime.date(2026, 6, 1))
        self.assertIn("not employed", str(caught.exception))

    def test_a_sheet_cannot_run_past_the_end_of_employment(self):
        leaver = self.employee("L1")
        leaver.termination_date = datetime.date(2026, 5, 31)
        leaver.employment_status = "terminated"
        leaver.save()
        with self.assertRaises(ValidationError) as caught:
            self.sheet(leaver)
        self.assertIn("not employed", str(caught.exception))


class NoDoubleClaimTests(TimesheetTestCase):
    def test_two_sheets_cannot_cover_the_same_week(self):
        # Both get approved, both feed payroll, and the hours are paid
        # twice with nothing about either sheet saying so.
        self.sheet()
        with self.assertRaises(ValidationError) as caught:
            self.sheet(start=datetime.date(2026, 6, 3), end=datetime.date(2026, 6, 10))
        self.assertIn("already has a timesheet", str(caught.exception))

    def test_a_rejected_sheet_frees_its_week(self):
        sheet = self.filled()
        sheet.submit()
        sheet.reject(by=self.boss)
        self.sheet(start=datetime.date(2026, 6, 1), end=datetime.date(2026, 6, 5))

    def test_the_next_week_is_fine(self):
        self.sheet()
        self.sheet(start=datetime.date(2026, 6, 8), end=datetime.date(2026, 6, 12))

    def test_two_people_may_both_file_for_one_week(self):
        self.sheet()
        self.sheet(self.employee("E2"))

    def test_time_cannot_be_claimed_for_a_day_taken_as_leave(self):
        # Both are claims on the same day and both feed pay. Each record
        # looks perfectly reasonable on its own.
        policy = LeavePolicy.objects.create(
            code="HOL", name="Holiday", leave_type="vacation", annual_days=Decimal("25")
        )
        leave = LeaveRequest.objects.create(
            employee=self.person, policy=policy, leave_type=LeaveType.VACATION,
            start_date=datetime.date(2026, 6, 2), end_date=datetime.date(2026, 6, 3),
        )
        leave.approve(by=self.boss)
        sheet = self.sheet()
        self.entry(sheet, datetime.date(2026, 6, 1))
        with self.assertRaises(ValidationError) as caught:
            self.entry(sheet, datetime.date(2026, 6, 2))
        self.assertIn("is on approved", str(caught.exception))

    def test_a_pending_leave_request_does_not_block_time(self):
        policy = LeavePolicy.objects.create(
            code="HOL", name="Holiday", leave_type="vacation", annual_days=Decimal("25")
        )
        LeaveRequest.objects.create(
            employee=self.person, policy=policy, leave_type=LeaveType.VACATION,
            start_date=datetime.date(2026, 6, 2), end_date=datetime.date(2026, 6, 3),
        )
        sheet = self.sheet()
        self.entry(sheet, datetime.date(2026, 6, 2))
        self.assertEqual(sheet.hours(), Decimal("8.00"))


class ApprovalTests(TimesheetTestCase):
    def test_an_empty_sheet_has_nothing_to_submit(self):
        sheet = self.sheet()
        with self.assertRaises(ValidationError) as caught:
            sheet.submit()
        self.assertIn("no time on this sheet", str(caught.exception))

    def test_nobody_approves_their_own_hours(self):
        sheet = self.filled()
        sheet.submit()
        with self.assertRaises(ValidationError) as caught:
            sheet.approve(by=self.person)
        self.assertIn("own hours", str(caught.exception))

    def test_a_draft_cannot_be_approved(self):
        sheet = self.filled()
        with self.assertRaises(ValidationError) as caught:
            sheet.approve(by=self.boss)
        self.assertIn("Only a submitted", str(caught.exception))

    def test_approved_hours_cannot_be_edited(self):
        sheet = self.filled()
        sheet.submit()
        sheet.approve(by=self.boss)
        entry = sheet.entries.first()
        entry.hours = Decimal("12")
        with self.assertRaises(ValidationError) as caught:
            entry.save()
        self.assertIn("can no longer change", str(caught.exception))

    def test_approved_hours_cannot_be_removed(self):
        sheet = self.filled()
        sheet.submit()
        sheet.approve(by=self.boss)
        with self.assertRaises(ValidationError):
            sheet.entries.first().delete()

    def test_an_approved_period_cannot_be_moved(self):
        sheet = self.filled()
        sheet.submit()
        sheet.approve(by=self.boss)
        sheet.period_end = datetime.date(2026, 6, 12)
        with self.assertRaises(ValidationError) as caught:
            sheet.save()
        self.assertIn("can no longer change", str(caught.exception))

    def test_an_approval_can_be_sent_back(self):
        # Without this an approval given in error can only be worked
        # around by raising a second sheet for the same week, which the
        # overlap check correctly refuses — leaving no way forward.
        sheet = self.filled()
        sheet.submit()
        sheet.approve(by=self.boss)
        sheet.send_back()
        self.assertEqual(sheet.status, TimesheetStatus.DRAFT)
        self.assertIsNone(sheet.decided_by)

    def test_a_sheet_sent_back_can_be_edited_again(self):
        sheet = self.filled()
        sheet.submit()
        sheet.approve(by=self.boss)
        sheet.send_back()
        entry = sheet.entries.first()
        entry.hours = Decimal("6")
        entry.save()
        self.assertEqual(sheet.hours(), Decimal("38.00"))

    def test_a_rejected_sheet_can_be_sent_back_too(self):
        sheet = self.filled()
        sheet.submit()
        sheet.reject(by=self.boss, note="wrong week")
        sheet.send_back()
        self.assertEqual(sheet.status, TimesheetStatus.DRAFT)


class FeedingPayrollTests(TimesheetTestCase):
    def setUp(self):
        super().setUp()
        self.hourly = PayComponent.objects.create(
            code="HR", name="Hourly pay", kind=ComponentKind.EARNING,
            basis=ComponentBasis.PER_HOUR, expense_account=self.wages, sequence=10,
        )
        EmployeeCompensation.objects.create(
            employee=self.person, component=self.hourly, amount=Decimal("25"),
            effective_from=datetime.date(2020, 1, 1),
        )

    def pay_run(self):
        return PayRun.objects.create(
            period_start=datetime.date(2026, 6, 1), period_end=datetime.date(2026, 6, 30),
            pay_date=datetime.date(2026, 6, 30),
        )

    def test_approved_hours_are_read_rather_than_handed_in(self):
        sheet = self.filled()
        sheet.submit()
        sheet.approve(by=self.boss)
        run = self.pay_run()
        run.calculate(employees=[self.person])
        self.assertEqual(run.payslips.get().gross(), Decimal("1000.00"))

    def test_hours_a_manager_has_not_signed_off_are_not_paid(self):
        # The whole reason the approval step exists.
        sheet = self.filled()
        sheet.submit()
        run = self.pay_run()
        with self.assertRaises(ValidationError) as caught:
            run.calculate(employees=[self.person])
        self.assertIn("no approved hours", str(caught.exception))

    def test_explicit_hours_still_override(self):
        run = self.pay_run()
        run.calculate(employees=[self.person], hours={self.person: Decimal("10")})
        self.assertEqual(run.payslips.get().gross(), Decimal("250.00"))

    def test_only_hours_inside_the_period_are_paid(self):
        sheet = self.filled()
        sheet.submit()
        sheet.approve(by=self.boss)
        later = self.sheet(start=datetime.date(2026, 7, 1), end=datetime.date(2026, 7, 3))
        for day in range(1, 4):
            self.entry(later, datetime.date(2026, 7, day))
        later.submit()
        later.approve(by=self.boss)
        run = self.pay_run()
        run.calculate(employees=[self.person])
        self.assertEqual(run.payslips.get().gross(), Decimal("1000.00"))

    def test_paid_hours_cannot_be_sent_back(self):
        # Editing them would leave a payslip nobody can reproduce.
        sheet = self.filled()
        sheet.submit()
        sheet.approve(by=self.boss)
        run = self.pay_run()
        run.calculate(employees=[self.person])
        run.post()
        with self.assertRaises(ValidationError) as caught:
            sheet.send_back()
        self.assertIn("already been paid", str(caught.exception))

    def test_voiding_the_run_frees_the_timesheet_again(self):
        sheet = self.filled()
        sheet.submit()
        sheet.approve(by=self.boss)
        run = self.pay_run()
        run.calculate(employees=[self.person])
        run.post()
        run.void()
        sheet.send_back()
        self.assertEqual(sheet.status, TimesheetStatus.DRAFT)


class ReportingTests(TimesheetTestCase):
    def test_approved_hours_in_a_range(self):
        sheet = self.filled()
        sheet.submit()
        sheet.approve(by=self.boss)
        self.assertEqual(
            approved_hours(self.person, datetime.date(2026, 6, 1), datetime.date(2026, 6, 3)),
            Decimal("24.00"),
        )

    def test_unapproved_time_is_not_counted(self):
        sheet = self.filled()
        sheet.submit()
        self.assertEqual(
            approved_hours(self.person, datetime.date(2026, 6, 1), datetime.date(2026, 6, 30)),
            Decimal("0"),
        )

    def test_time_can_be_grouped_by_what_it_was_charged_to(self):
        sheet = self.sheet()
        self.entry(sheet, datetime.date(2026, 6, 1), "8", account=self.project_a)
        self.entry(sheet, datetime.date(2026, 6, 2), "5", account=self.project_a)
        self.entry(sheet, datetime.date(2026, 6, 3), "7", account=self.project_b)
        sheet.submit()
        sheet.approve(by=self.boss)
        rows = {row["code"]: row["hours"] for row in hours_by_account(
            datetime.date(2026, 6, 1), datetime.date(2026, 6, 30)
        )}
        self.assertEqual(rows["7100"], Decimal("13.00"))
        self.assertEqual(rows["7200"], Decimal("7.00"))

    def test_billable_time_can_be_asked_for_on_its_own(self):
        sheet = self.sheet()
        self.entry(sheet, datetime.date(2026, 6, 1), "8",
                   account=self.project_a, is_billable=True)
        self.entry(sheet, datetime.date(2026, 6, 2), "5", account=self.project_a)
        sheet.submit()
        sheet.approve(by=self.boss)
        rows = hours_by_account(
            datetime.date(2026, 6, 1), datetime.date(2026, 6, 30), billable=True
        )
        self.assertEqual(rows[0]["hours"], Decimal("8.00"))
