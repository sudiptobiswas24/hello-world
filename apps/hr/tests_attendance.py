"""
October 2026, six days a week: 27 working days (Sundays the 4th, 11th,
18th and 25th off). A worker on 13,500 a month is absent twice and in
for half a day once, so 2.5 unpaid days: 13,500 × 24.5 / 27 = 12,250.00.
Six hours of overtime at 125 is 750. A day-rated worker with a working
day unmarked stops the run; the register inside a posted run is kept.

The clock wraps: 00:10 is 130 minutes late for a 22:00 shift, 21:50 is
on time, and 06:17 is 17 minutes late for 06:00.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from .attendance import AttendanceDay, AttendanceStatus, import_punches, minutes_late, unmarked_days
from .models import LeavePolicy, LeaveRequest, LeaveType
from .payroll import ComponentBasis, ComponentKind, PayComponent, PayRunStatus
from .tests_payroll import PayrollTestCase

D = datetime.date
OCT = (D(2026, 10, 1), D(2026, 10, 31))


class AttendanceTestCase(PayrollTestCase):
    def setUp(self):
        super().setUp()
        self.boss = self.employee("B1")
        self.worker = self.employee("W1", working_days="123456", manager=self.boss)
        self.pay(self.worker, self.salary, "13500")
        self.overtime = PayComponent.objects.create(
            code="OT", name="Overtime", kind=ComponentKind.EARNING, basis=ComponentBasis.PER_OVERTIME_HOUR,
            expense_account=self.wages, sequence=15)
        self.pay(self.worker, self.overtime, "125")

    def mark(self, day, status=AttendanceStatus.PRESENT, person=None, **extra):
        return AttendanceDay.objects.create(employee=person or self.worker, on=D(2026, 10, day), status=status,
                                            **extra)

    def october(self):
        return self.pay_run(*OCT, pay_date=OCT[1])


class RefusedTests(AttendanceTestCase):
    def test_once_a_day_and_not_before_they_joined(self):
        self.mark(5)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.mark(5)
        late_joiner = self.employee("W2", hire=D(2026, 10, 15))
        with self.assertRaisesMessage(ValidationError, "was not employed on 2026-10-05"):
            self.mark(5, person=late_joiner)

    def test_absent_has_no_time_in_and_no_overtime(self):
        with self.assertRaisesMessage(ValidationError, "no time in and no overtime"):
            self.mark(5, AttendanceStatus.ABSENT, overtime_hours=Decimal("2"))

    def test_not_on_a_day_of_approved_leave(self):
        policy = LeavePolicy.objects.create(code="HOL", name="Holiday", leave_type="vacation", annual_days=Decimal("12"))
        leave = LeaveRequest.objects.create(employee=self.worker, policy=policy, leave_type=LeaveType.VACATION,
                                            start_date=D(2026, 10, 6), end_date=D(2026, 10, 6))
        leave.approve(by=self.boss)
        with self.assertRaisesMessage(ValidationError, "is on approved leave on 2026-10-06"):
            self.mark(6)

    def test_overtime_is_an_earning(self):
        with self.assertRaisesMessage(ValidationError, "overtime is something earned"):
            PayComponent.objects.create(code="OTD", name="Wrong", kind=ComponentKind.DEDUCTION,
                                        basis=ComponentBasis.PER_OVERTIME_HOUR, liability_account=self.tax_payable)


class LateTests(AttendanceTestCase):
    def test_minutes_after_the_start_on_a_clock_that_wraps(self):
        self.assertEqual(minutes_late(datetime.time(6), datetime.time(6, 17)), 17)
        self.assertEqual(minutes_late(datetime.time(22), datetime.time(0, 10)), 130)
        self.assertEqual(minutes_late(datetime.time(22), datetime.time(21, 50)), 0)


class OnThePayslipTests(AttendanceTestCase):
    def test_absences_and_half_days_come_off_the_salary_and_overtime_is_paid(self):
        self.mark(5, AttendanceStatus.ABSENT)
        self.mark(6, AttendanceStatus.ABSENT)
        self.mark(7, AttendanceStatus.HALF_DAY, time_in=datetime.time(6))
        self.mark(8, overtime_hours=Decimal("4"))
        self.mark(9, overtime_hours=Decimal("2"))
        run = self.october()
        run.calculate()
        lines = {line.component.code: line.amount for line in run.payslips.get().lines.all()}
        self.assertEqual((lines["SAL"], lines["OT"]), (Decimal("12250.00"), Decimal("750.00")))

    def test_an_absence_on_a_sunday_costs_nothing(self):
        self.mark(4, AttendanceStatus.ABSENT)
        run = self.october()
        run.calculate()
        self.assertEqual(run.payslips.get().lines.get(component=self.salary).amount, Decimal("13500.00"))

    def test_a_day_rated_worker_with_a_day_unmarked_stops_the_run(self):
        self.worker.paid_by_attendance = True
        self.worker.save()
        for day in range(1, 32):
            if D(2026, 10, day).isoweekday() != 7 and day != 20:
                self.mark(day)
        self.assertEqual(unmarked_days(self.worker, *OCT), [D(2026, 10, 20)])
        run = self.october()
        with self.assertRaisesMessage(ValidationError, "1 working day(s) in this period are not marked, the first 2026-10-20"):
            run.calculate()
        self.mark(20, AttendanceStatus.ABSENT)
        run.calculate()
        self.assertEqual(run.payslips.get().lines.get(component=self.salary).amount, Decimal("13000.00"))

    def test_the_register_inside_a_posted_run_is_kept(self):
        marked = self.mark(5, AttendanceStatus.ABSENT)
        run = self.october()
        run.calculate()
        run.post()
        self.assertEqual(run.status, PayRunStatus.POSTED)
        with self.assertRaisesMessage(ValidationError, "is posted and paid on this register"):
            self.mark(6, AttendanceStatus.ABSENT)
        marked.status = AttendanceStatus.PRESENT
        with self.assertRaisesMessage(ValidationError, "is posted and paid on this register"):
            marked.save()
        with self.assertRaisesMessage(ValidationError, "is posted and paid on this register"):
            marked.delete()
        self.assertEqual(self.mark(5, AttendanceStatus.ABSENT, person=self.boss).status, AttendanceStatus.ABSENT)


PUNCHES = """employee_number,date,shift,in,out
W1,2026-10-05,,06:03,14:10
W1,2026-10-06,,06:00,
"""


class PunchFileTests(AttendanceTestCase):
    def test_a_dry_run_keeps_nothing_and_a_commit_keeps_the_whole_days(self):
        report = import_punches(PUNCHES)
        self.assertEqual((report["rows"], report["created"], report["errors"]), (2, 1, []))
        self.assertEqual([row[0] for row in report["to_enter"]], [3])
        self.assertFalse(AttendanceDay.objects.exists())
        import_punches(PUNCHES, commit=True)
        day = AttendanceDay.objects.get()
        self.assertEqual((day.on, day.status, day.time_out, day.source),
                         (D(2026, 10, 5), AttendanceStatus.PRESENT, datetime.time(14, 10), "import"))

    def test_a_bad_row_keeps_the_whole_file_out(self):
        report = import_punches(PUNCHES + "W9,2026-10-07,,06:00,14:00\n", commit=True)
        self.assertEqual(report["errors"], [(4, "employee_number", "No employee numbered 'W9'.")])
        self.assertFalse(AttendanceDay.objects.exists())

    def test_a_day_entered_by_hand_is_kept(self):
        self.mark(5, AttendanceStatus.HALF_DAY, note="Sent home ill")
        report = import_punches(PUNCHES, commit=True)
        self.assertEqual(report["created"], 0)
        self.assertEqual(len(report["kept"]), 1)
        self.assertEqual(AttendanceDay.objects.get(on=D(2026, 10, 5)).status, AttendanceStatus.HALF_DAY)


class AttendanceApiTests(AttendanceTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_supervisor_marks_and_payroll_reads_the_gaps(self):
        self.worker.paid_by_attendance = True
        self.worker.save()
        supervisor = self.as_("Production Supervisor")
        made = supervisor.post("/api/hr/attendance/", {
            "employee": self.worker.pk, "on": "2026-10-05", "status": "present", "time_in": "06:17",
            "time_out": "14:05", "overtime_hours": "0"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        payroll = self.as_("Payroll Officer")
        [row] = payroll.get("/api/hr/attendance/unmarked/", {"start": "2026-10-05", "end": "2026-10-07"}).json()
        self.assertEqual((row["employee_number"], row["days"]), ("W1", ["2026-10-06", "2026-10-07"]))
        checked = payroll.post("/api/hr/attendance/punches/", {"text": PUNCHES}, format="json").json()
        self.assertEqual((checked["created"], checked["committed"]), (0, False))
        self.assertEqual(len(checked["kept"]), 1)
        self.assertEqual(self.as_("Warehouse Staff").get("/api/hr/attendance/").status_code, 403)
