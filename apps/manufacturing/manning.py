"""
Who runs the machines: a bank's crew, shift by shift.

Twelve looms with three weavers on the night shift are twelve looms of
capacity on paper and twelve looms' worth of rolls nobody wove. A weaver
minds four looms, a BCS needs two people, a tape line three: the bank
says how many operators a machine takes, the crew says who is on it, and
what can run is the lesser of the machines in service and the machines
the crew present can run.

**Derived, not stored.** The heads on a shift are counted from who is
assigned, employed, and not on approved leave that day: half a day's
leave is half a person. Nothing records "the night crew is three",
which would be wrong the first time somebody went on leave.

**Only where it is stated.** A bank that does not say how many operators
a machine takes is not limited by its crew — which is how every bank
behaved before this. One that says so and has nobody assigned has
nobody to run it, and capacity says nought: that is the honest answer,
and the planner sees it.
"""

from decimal import ROUND_FLOOR, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import AuditModel

ZERO = Decimal("0")
HALF = Decimal("0.5")


class CrewAssignment(AuditModel):
    employee = models.ForeignKey("hr.Employee", on_delete=models.PROTECT,
                                 related_name="crew_assignments")
    work_centre = models.ForeignKey("manufacturing.WorkCentre", on_delete=models.PROTECT,
                                    related_name="crew")
    shift = models.ForeignKey("manufacturing.Shift", on_delete=models.PROTECT,
                              related_name="crew")
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["work_centre", "shift", "employee"]
        constraints = [
            models.CheckConstraint(
                check=Q(valid_to__isnull=True) | Q(valid_to__gte=models.F("valid_from")),
                name="crew_assignment_window_forwards"),
        ]

    def __str__(self):
        return f"{self.employee} on {self.work_centre.code} {self.shift.code}"

    def save(self, *args, **kwargs):
        # One person in two crews at once is counted twice.
        clash = CrewAssignment.objects.filter(employee=self.employee).exclude(pk=self.pk)
        clash = clash.filter(Q(valid_to__isnull=True) | Q(valid_to__gte=self.valid_from))
        if self.valid_to is not None:
            clash = clash.filter(valid_from__lte=self.valid_to)
        other = clash.first()
        if other is not None:
            raise ValidationError(f"{self.employee} is already on {other.work_centre.code} "
                                  f"{other.shift.code} from {other.valid_from}; end that first.")
        super().save(*args, **kwargs)


def heads(centre, shift, day):
    """People on this bank's shift that day: assigned, employed, not away."""
    from apps.hr.models import LeaveRequest, LeaveStatus

    total = ZERO
    rows = CrewAssignment.objects.filter(work_centre=centre, shift=shift, valid_from__lte=day)
    rows = rows.filter(Q(valid_to__isnull=True) | Q(valid_to__gte=day)).select_related(
        "employee")
    for row in rows:
        if not row.employee.is_working_on(day):
            continue
        leave = LeaveRequest.objects.filter(
            employee=row.employee, status=LeaveStatus.APPROVED, start_date__lte=day,
            end_date__gte=day).first()
        if leave is None:
            total += 1
        elif leave.half_day:
            total += HALF
    return total


def manned_machines(centre, shift, day):
    """Machines the crew present can run, or None where the bank is not crewed."""
    per = centre.operators_per_machine
    if per is None:
        return None
    return int((heads(centre, shift, day) / per).to_integral_value(rounding=ROUND_FLOOR))


def manned_minutes(centre, day):
    """
    Minutes this bank's crews can run its machines on a day, or None
    where the bank is not crewed: each active shift, the machines the
    crew can run (never more than are in service that day) times the
    shift's hours.
    """
    from .shifts import Shift

    if centre.operators_per_machine is None:
        return None
    machines = centre.machine_list()
    in_service = (sum(1 for m in machines if m.calendar().is_working(day)) if machines
                  else (0 if centre.runs_on_machines()
                        else int(centre.calendar().is_working(day))))
    total = ZERO
    for shift in Shift.objects.filter(is_active=True):
        crewed = manned_machines(centre, shift, day)
        total += min(crewed, in_service) * shift.hours * 60
    return total
