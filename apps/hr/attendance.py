"""
Who came to which shift: present, half a day or absent, when they
clocked in and out, how late, and the overtime agreed for the day.

An absence is unpaid wherever it falls on a day the person works and is
not covered by approved leave, so salary comes off for it as it does for
unpaid leave. Leave asked for after the absence — the usual order for a
sick day — is what excuses it: paid or not as its policy says, and not
docked a second time off the register. Half a day off leaves the other
half to be worked, or not, and marked either way. A day-rated worker (`Employee.paid_by_attendance`) is paid
for what the register shows and nothing else, so a pay run refuses while
one of their working days is unmarked: an empty day read as present is
a day's wage paid on nobody's word.

A day inside a posted pay run is what that run paid on. It is not
changed, added or taken off until the run is voided.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum

from apps.core.csvrows import RowError, date, read
from apps.core.models import AuditModel, lock_rows

from .calendars import holidays_between, parse_working_days
from .models import Employee, LeaveRequest, LeaveStatus

# What a shift's code means on the clock, registered by the module that
# keeps the shifts (manufacturing), so hr imports none of it.
# lookup(code) -> starts_at (a time), or None for a code it does not know.
SHIFT_CLOCKS = []


def register_shift_clock(lookup):
    SHIFT_CLOCKS.append(lookup)


def shift_starts(code):
    for lookup in SHIFT_CLOCKS:
        found = lookup(code)
        if found is not None:
            return found
    return None


def minutes_late(starts_at, clocked_in):
    """Minutes after the start, on a clock that wraps: 00:10 is 130 minutes late for 22:00, 21:50 is on time."""
    start = starts_at.hour * 60 + starts_at.minute
    came = clocked_in.hour * 60 + clocked_in.minute
    after = (came - start) % (24 * 60)
    return 0 if after > 12 * 60 else after



class AttendanceSource(models.TextChoices):
    MANUAL = "manual", "Entered"
    IMPORT = "import", "From the punch file"

class AttendanceStatus(models.TextChoices):
    PRESENT = "present", "Present"
    HALF_DAY = "half_day", "Half a day"
    ABSENT = "absent", "Absent"


# What a day counts as unpaid, by status.
ZERO, HALF, ONE = Decimal("0"), Decimal("0.5"), Decimal("1")
UNPAID = {AttendanceStatus.ABSENT: ONE, AttendanceStatus.HALF_DAY: HALF}


class AttendanceDay(AuditModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendance")
    on = models.DateField()
    status = models.CharField(max_length=16, choices=AttendanceStatus.choices)
    shift = models.CharField(max_length=16, blank=True, help_text="The shift's code, as the floor names it.")
    time_in = models.TimeField(null=True, blank=True)
    time_out = models.TimeField(null=True, blank=True)
    late_minutes = models.PositiveSmallIntegerField(
        default=0, help_text="Worked out from the shift's start where the shift and the time in are known.")
    overtime_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"),
                                         help_text="Overtime agreed for the day, paid at the overtime rate.")
    source = models.CharField(max_length=16, default=AttendanceSource.MANUAL, editable=False,
                              choices=AttendanceSource.choices)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["on", "employee_id"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "on"], name="one_attendance_a_day"),
            models.CheckConstraint(check=Q(overtime_hours__gte=0) & Q(overtime_hours__lte=24),
                                   name="attendance_overtime_in_a_day"),
        ]

    def __str__(self):
        return f"{self.employee} {self.on} {self.get_status_display()}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        # Leave is approved against the register under the same lock: the
        # day marked as the leave is approved would find no leave, and the
        # leave no mark.
        lock_rows(self.employee, refresh=False)
        if not self.employee.is_employed_on(self.on):
            raise ValidationError({"on": f"{self.employee} was not employed on {self.on}."})
        if self.status == AttendanceStatus.ABSENT and (self.time_in or self.overtime_hours):
            raise ValidationError("Somebody absent has no time in and no overtime.")
        leave = leave_on(self.employee, self.on)
        if leave is not None and leave.half_day and self.status == AttendanceStatus.PRESENT:
            raise ValidationError({"on": f"{self.employee} has half of {self.on} off as approved leave; "
                                         "mark the half they came in for as half a day."})
        if leave is not None and not leave.half_day:
            raise ValidationError({"on": f"{self.employee} is on approved leave on {self.on}; "
                                         "cancel the leave first if they came in."})
        if not self._state.adding:
            # The day as it stood is what a posted run may have paid on, as much as the new one.
            before = AttendanceDay.objects.get(pk=self.pk)
            _refuse_if_paid(before.employee, before.on)
        _refuse_if_paid(self.employee, self.on)
        starts = shift_starts(self.shift) if self.shift else None
        if self.shift and SHIFT_CLOCKS and starts is None:
            raise ValidationError({"shift": f"No shift {self.shift!r}."})
        if starts is not None and self.time_in:
            self.late_minutes = minutes_late(starts, self.time_in)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _refuse_if_paid(self.employee, self.on)
        return super().delete(*args, **kwargs)


def _refuse_if_paid(employee, on):
    from .payroll import Payslip, PayRunStatus

    paid = Payslip.objects.filter(employee=employee, run__status=PayRunStatus.POSTED,
                                  run__period_start__lte=on, run__period_end__gte=on).select_related("run").first()
    if paid is not None:
        raise ValidationError(f"{on} is inside {paid.run}, which is posted and paid on this register; "
                              "void the run to change it.")


def leave_on(employee, on):
    """The approved leave covering a day, or None."""
    return LeaveRequest.objects.filter(employee=employee, status=LeaveStatus.APPROVED,
                                       start_date__lte=on, end_date__gte=on).first()


def leave_cover(employee, start, end):
    """{day: how much of it approved leave takes off, a whole day or a half}, between two dates."""
    covered = {}
    for first, last, half in LeaveRequest.objects.filter(
            employee=employee, status=LeaveStatus.APPROVED, start_date__lte=end, end_date__gte=start,
    ).values_list("start_date", "end_date", "half_day"):
        day = max(first, start)
        while day <= min(last, end):
            covered[day] = HALF if half else ONE
            day += datetime.timedelta(days=1)
    return covered


def works_on(employee, day, holidays):
    return day.isoweekday() in parse_working_days(employee.working_days) and day not in holidays


def _employed_span(employee, start, end):
    start = max(start, employee.hire_date)
    if employee.termination_date:
        end = min(end, employee.termination_date)
    return start, end


def absent_days(employee, start, end):
    """
    Unpaid days the register shows: an absence a whole day, half a day a
    half, on days the person works, less what approved leave covers. A
    covered absence is the leave's: docked here as well, an unpaid sick day
    came off the salary twice and a paid one came off it at all.
    """
    holidays = holidays_between(start, end, employee.holiday_region)
    covered = leave_cover(employee, start, end)
    return sum((max(UNPAID[row.status] - covered.get(row.on, ZERO), ZERO) for row in AttendanceDay.objects.filter(
        employee=employee, on__range=(start, end), status__in=list(UNPAID))
        if works_on(employee, row.on, holidays)), ZERO)


def unmarked_days(employee, start, end):
    """
    Working days in the span, while employed, with neither a register entry
    nor a whole day's approved leave. Half a day off leaves the other half
    for the register to say.
    """
    start, end = _employed_span(employee, start, end)
    if end < start:
        return []
    holidays = holidays_between(start, end, employee.holiday_region)
    marked = set(AttendanceDay.objects.filter(employee=employee, on__range=(start, end))
                 .values_list("on", flat=True))
    covered = leave_cover(employee, start, end)
    days, day = [], start
    while day <= end:
        if works_on(employee, day, holidays) and day not in marked and covered.get(day) != ONE:
            days.append(day)
        day += datetime.timedelta(days=1)
    return days


def unmarked_report(start, end):
    """Each day-rated person with days unmarked in the span, and the days: what a pay run would refuse on."""
    rows = []
    for employee in Employee.objects.filter(paid_by_attendance=True).select_related("party"):
        days = unmarked_days(employee, start, end)
        if days:
            rows.append({"employee": employee.pk, "employee_number": employee.employee_number,
                         "employee_name": employee.party.name, "days": days})
    return rows


def overtime_hours(employee, start, end):
    return AttendanceDay.objects.filter(employee=employee, on__range=(start, end)).aggregate(
        hours=Sum("overtime_hours"))["hours"] or Decimal("0")


PUNCH_COLUMNS = ["employee_number", "date", "shift", "in", "out"]


def import_punches(text, commit=False):
    """
    A punch file from the biometric reader: one row a person a day, with
    the time in and out, which makes the day present. A row with only one
    punch is somebody who forgot the other: it is not guessed at, but
    listed to be entered by hand. The whole file or none of it: every row
    is checked, and nothing is kept unless all are clean and `commit` is
    set. A day already entered by hand is left as entered and said so.

    Returns {"rows", "created", "updated", "kept", "to_enter", "errors": [(row, column, message)]}.
    """
    rows = read(text)
    report = {"rows": len(rows), "created": 0, "updated": 0, "kept": [], "to_enter": [], "errors": []}
    employees = {row.employee_number: row for row in Employee.objects.filter(
        employee_number__in={row.get("employee_number", "") for row in rows})}
    with transaction.atomic():
        for number, row in enumerate(rows, start=2):
            try:
                _punch(row, number, employees, report)
            except RowError as error:
                report["errors"].append((number, error.column, error.message))
            except ValidationError as error:
                report["errors"].append((number, "", " ".join(error.messages)))
        if report["errors"] or not commit:
            transaction.set_rollback(True)
    return report


def _time(row, column):
    value = row.get(column, "")
    if not value:
        return None
    try:
        return datetime.time.fromisoformat(value if len(value) > 4 else value.zfill(5))
    except ValueError:
        raise RowError(column, f"{value!r} is not a time; give it as HH:MM.") from None


def _punch(row, number, employees, report):
    employee = employees.get(row.get("employee_number", ""))
    if employee is None:
        raise RowError("employee_number", f"No employee numbered {row.get('employee_number')!r}.")
    day = date(row, "date")
    time_in, time_out = _time(row, "in"), _time(row, "out")
    if time_in is None and time_out is None:
        raise RowError("in", "A punch row has a time in, a time out or both.")
    if time_in is None or time_out is None:
        report["to_enter"].append((number, f"{employee} on {day} punched {'in' if time_in else 'out'} only."))
        return
    existing = AttendanceDay.objects.filter(employee=employee, on=day).first()
    if existing is not None and existing.source == AttendanceSource.MANUAL:
        report["kept"].append((number, f"{employee} on {day} was entered by hand and is kept as entered."))
        return
    values = dict(status=AttendanceStatus.PRESENT, shift=row.get("shift", ""), time_in=time_in, time_out=time_out)
    if existing is None:
        AttendanceDay(employee=employee, on=day, source=AttendanceSource.IMPORT, **values).save()
        report["created"] += 1
    else:
        for name, value in values.items():
            setattr(existing, name, value)
        existing.save()
        report["updated"] += 1

