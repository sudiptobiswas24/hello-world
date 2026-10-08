"""
A day claimed twice: off as leave, and in the register or on a timesheet.

October 2026, six days a week: 27 working days, and 13,500 a month is
500 a day. W1 is absent on Monday 5 October and asks for the day as
leave afterwards, which is the order a sick day comes in. Unpaid, the
day comes off once: 13,000.00, not 12,500.00. Paid, it does not come
off at all: 13,500.00, not 13,000.00.

Half a day unpaid is 250: 13,250.00. Half a day paid with the other
half marked as half a day is a full day's pay, 13,500.00; with the
other half absent, 250 comes off: 13,250.00.

A day marked present, or with hours on a timesheet, is not then taken
off: whichever came first stands, and the other is refused. Sunday the
4th is not a working day, so coming in on it is no claim on a week off.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from .attendance import AttendanceStatus, unmarked_days
from .models import LeavePolicy, LeaveRequest, LeaveType
from .tests_attendance import OCT, AttendanceTestCase
from .timesheets import Timesheet, TimesheetEntry

D = datetime.date


class TheDayTestCase(AttendanceTestCase):
    def leave(self, first, last=None, paid=True, half=False):
        policy, _ = LeavePolicy.objects.get_or_create(
            code="PAID" if paid else "UNPAID",
            defaults={"name": "Leave", "leave_type": "other", "annual_days": Decimal("12"), "is_paid": paid})
        return LeaveRequest.objects.create(
            employee=self.worker, policy=policy, leave_type=LeaveType.OTHER,
            start_date=D(2026, 10, first), end_date=D(2026, 10, last or first), half_day=half)

    def october_pay(self):
        run = self.october()
        run.calculate()
        return run.payslips.get().lines.get(component=self.salary).amount

    def hours(self, day, hours="8"):
        sheet, _ = Timesheet.objects.get_or_create(employee=self.worker, period_start=D(2026, 10, 1),
                                                   period_end=D(2026, 10, 31))
        return TimesheetEntry.objects.create(timesheet=sheet, date=D(2026, 10, day), hours=Decimal(hours))


class AnAbsenceExcusedAfterwardsTests(TheDayTestCase):
    def test_unpaid_leave_for_it_comes_off_once(self):
        self.mark(5, AttendanceStatus.ABSENT)
        self.leave(5, paid=False).approve(by=self.boss)
        self.assertEqual(self.october_pay(), Decimal("13000.00"))

    def test_paid_leave_for_it_does_not_come_off(self):
        self.mark(5, AttendanceStatus.ABSENT)
        self.leave(5, paid=True).approve(by=self.boss)
        self.assertEqual(self.october_pay(), Decimal("13500.00"))


class HalfADayOffTests(TheDayTestCase):
    def test_half_a_day_unpaid_is_half_a_days_pay(self):
        self.leave(6, paid=False, half=True).approve(by=self.boss)
        self.assertEqual(self.october_pay(), Decimal("13250.00"))

    def test_the_other_half_is_marked_and_paid(self):
        self.leave(6, paid=True, half=True).approve(by=self.boss)
        with self.assertRaisesMessage(ValidationError, "mark the half they came in for as half a day"):
            self.mark(6)
        self.mark(6, AttendanceStatus.HALF_DAY, time_in=datetime.time(13))
        self.assertEqual(self.october_pay(), Decimal("13500.00"))

    def test_the_other_half_away_comes_off(self):
        self.leave(6, paid=True, half=True).approve(by=self.boss)
        self.mark(6, AttendanceStatus.ABSENT)
        self.assertEqual(self.october_pay(), Decimal("13250.00"))

    def test_a_day_rated_worker_has_the_other_half_marked(self):
        self.worker.paid_by_attendance = True
        self.worker.save()
        self.leave(6, paid=True, half=True).approve(by=self.boss)
        self.leave(7, paid=True).approve(by=self.boss)
        # The whole day off needs no mark; the half day's other half does.
        self.assertIn(D(2026, 10, 6), unmarked_days(self.worker, *OCT))
        self.assertNotIn(D(2026, 10, 7), unmarked_days(self.worker, *OCT))

    def test_the_other_half_is_claimed_on_a_timesheet(self):
        self.leave(6, paid=True, half=True).approve(by=self.boss)
        self.assertEqual(self.hours(6, "4").hours, Decimal("4"))


class ADayWorkedIsNotTakenOffTests(TheDayTestCase):
    def test_not_over_a_day_marked_present(self):
        self.mark(7)
        request = self.leave(5, 9)
        with self.assertRaisesMessage(ValidationError, "is marked present on 2026-10-07: a day is not both worked"):
            request.approve(by=self.boss)
        request.refresh_from_db()
        self.assertEqual(request.status, "pending")

    def test_not_a_whole_day_over_half_a_day_worked(self):
        self.mark(7, AttendanceStatus.HALF_DAY, time_in=datetime.time(6))
        whole = self.leave(7)
        with self.assertRaisesMessage(ValidationError, "is marked half a day on 2026-10-07"):
            whole.approve(by=self.boss)
        whole.cancel()
        # Half a day off is what the other half was.
        self.leave(7, half=True).approve(by=self.boss)

    def test_not_over_hours_on_a_timesheet(self):
        self.hours(8)
        whole = self.leave(8)
        with self.assertRaisesMessage(ValidationError, "has 8.00 hours on a timesheet for 2026-10-08"):
            whole.approve(by=self.boss)
        whole.cancel()
        self.leave(8, half=True).approve(by=self.boss)

    def test_a_sunday_worked_is_no_claim_on_the_week_off(self):
        self.mark(4)
        self.hours(4)
        self.leave(1, 10).approve(by=self.boss)

    def test_an_absence_is_what_leave_excuses(self):
        self.mark(5, AttendanceStatus.ABSENT)
        self.leave(5, 9).approve(by=self.boss)

    def test_through_the_api_the_manager_is_told_why(self):
        call_command("setup_roles", verbosity=0)
        self.mark(7)
        request = self.leave(7)
        manager = User.objects.create_user("w1-manager")
        manager.groups.add(Group.objects.get(name="Line Manager"))
        self.boss.user = manager
        self.boss.save()
        api = APIClient()
        api.force_authenticate(manager)
        response = api.post(f"/api/hr/leave-requests/{request.pk}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("a day is not both worked and taken off", str(response.content))
        request.refresh_from_db()
        self.assertEqual(request.status, "pending")


class UnderAPostedRunTests(TheDayTestCase):
    """
    October posted: what it paid on is what the leave said then. The
    register refuses a change inside a posted run; so does the leave,
    where pay read it.
    """

    def posted(self):
        run = self.october()
        run.calculate()
        run.post()
        return run

    def test_unpaid_leave_is_not_approved_in_a_month_already_paid(self):
        self.posted()
        with self.assertRaisesMessage(ValidationError, "void the run to approve this leave"):
            self.leave(6, paid=False).approve(by=self.boss)

    def test_paid_leave_does_not_excuse_an_absence_already_docked(self):
        self.mark(5, AttendanceStatus.ABSENT)
        self.posted()
        with self.assertRaisesMessage(ValidationError, "void the run to approve this leave"):
            self.leave(5).approve(by=self.boss)

    def test_paid_leave_on_a_day_the_run_read_nothing_for(self):
        self.posted()
        self.leave(6).approve(by=self.boss)

    def test_unpaid_leave_already_docked_is_not_sent_back_or_cancelled(self):
        request = self.leave(6, paid=False)
        request.approve(by=self.boss)
        self.posted()
        with self.assertRaisesMessage(ValidationError, "void the run to send back this leave"):
            LeaveRequest.objects.get(pk=request.pk).withdraw_approval(by=self.boss)
        with self.assertRaisesMessage(ValidationError, "void the run to cancel this leave"):
            LeaveRequest.objects.get(pk=request.pk).cancel(on_date=D(2026, 10, 1))
        request.refresh_from_db()
        self.assertEqual(request.status, "approved")

    def test_a_day_rated_workers_day_off_was_paid_on_the_leaves_word(self):
        self.worker.paid_by_attendance = True
        self.worker.save()
        request = self.leave(20)
        request.approve(by=self.boss)
        for day in range(1, 32):
            if D(2026, 10, day).isoweekday() != 7 and day != 20:
                self.mark(day)
        self.posted()
        with self.assertRaisesMessage(ValidationError, "void the run to send back this leave"):
            LeaveRequest.objects.get(pk=request.pk).withdraw_approval(by=self.boss)
