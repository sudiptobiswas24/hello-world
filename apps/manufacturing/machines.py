"""
One loom, one extruder, one printing press — by name.

A work centre used to be both the bank and the machine, and its own
docstring claimed that "which loom is the identity of a run rather
than a note on it". Nothing recorded which loom. That is the shape
this project keeps hitting: a comment stating a fact the schema cannot
hold. Either the claim goes or the field arrives, and here the claim
was right.

The distinction that makes this worth a model:

    work centre   what a routing names. "Weaving." A planner writes a
                  routing once and it has to hold for every run, so it
                  cannot name loom seventeen.
    machine       what actually ran it. Chosen when the run is
                  scheduled, or recorded when the shift is booked.

So the routing points at the bank and the booking points at the
machine, and the bank's capacity is the sum of what its machines can
do rather than a number somebody typed on the bank.

**A centre with no machines behaves exactly as it did.** That is
deliberate and not a transition measure. A single-machine centre — one
laminator, one bagging line — has nothing to gain from a second row
saying so, and forcing one on every plant that does not need it is
ceremony. Capacity falls back to the centre's own hours the moment the
centre has no machines listed.

Everything a machine can do is derived from its calendar and its
hours. Nothing here stores availability, because a machine that was
put on a six-day pattern last month did not retrospectively work the
Sundays before it.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import AuditModel
from apps.hr.calendars import WorkingCalendar, parse_working_days

MINUTES_PER_HOUR = Decimal("60")
ZERO = Decimal("0")


class Machine(AuditModel):
    """
    A named machine inside a work centre.

    Its hours, its pattern and its speed all fall back to the centre's
    when it does not state its own, because the common case is a bank
    of identical looms and repeating the same three numbers forty times
    is how they drift apart.

    What it does NOT carry is a rate per hour. Absorption is a
    work-centre rate here, on purpose: a plant argues about machine,
    labour and overhead rates at the level it sets budgets, which is
    the bank and not the individual loom. A new loom that genuinely
    costs a different hour than an old one belongs in its own centre,
    and saying so in the chart is better than a rate nobody reconciles.
    """

    work_centre = models.ForeignKey(
        "manufacturing.WorkCentre", on_delete=models.PROTECT,
        related_name="machines",
        help_text="The bank this machine belongs to. A routing names the "
                  "bank; a run names the machine.",
    )
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, blank=True)
    capacity_per_hour = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="What this one does an hour, where it differs from the "
                  "bank's nominal rate. An older loom at ninety picks a "
                  "minute against a newer one at a hundred and ten is the "
                  "reason this is not a property of the bank.",
    )
    capacity_uom = models.ForeignKey(
        "core.UnitOfMeasure", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    available_hours_per_day = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Hours this machine runs on a day it runs at all. Blank "
                  "takes the work centre's.",
    )
    working_days = models.CharField(
        max_length=7, blank=True,
        help_text="Which days this one runs, as ISO weekday numbers. Blank "
                  "takes the work centre's — which is the usual case, and "
                  "the exception is the loom kept on days only because "
                  "there is nobody to mind it at night.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="A machine that has been sold or scrapped. Inactive takes "
                  "it out of the bank's capacity from now on and leaves "
                  "every run it ever made pointing at it.",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["work_centre", "code"]
        constraints = [
            models.CheckConstraint(
                check=Q(available_hours_per_day__isnull=True)
                | (
                    Q(available_hours_per_day__gt=0)
                    & Q(available_hours_per_day__lte=24)
                ),
                name="machine_hours_in_a_day",
            ),
            models.CheckConstraint(
                check=Q(capacity_per_hour__isnull=True)
                | Q(capacity_per_hour__gt=0),
                name="machine_rate_positive",
            ),
            models.CheckConstraint(
                check=Q(capacity_per_hour__isnull=True)
                | Q(capacity_uom__isnull=False),
                name="machine_rate_says_what_it_counts",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}" if self.name else self.code

    def clean(self):
        # Parsed where somebody types it rather than where a plan
        # divides by it, which is the same reason the work centre
        # parses its own.
        if self.working_days:
            parse_working_days(self.working_days)

    def save(self, *args, **kwargs):
        # `clean()` is not called for you, and machines are created in
        # code by every fixture and import that sets a plant up.
        if self.working_days:
            parse_working_days(self.working_days)
        super().save(*args, **kwargs)

    # -- what it falls back to ------------------------------------------

    def days_pattern(self):
        return self.working_days or self.work_centre.working_days

    def hours_per_day(self):
        if self.available_hours_per_day is not None:
            return Decimal(self.available_hours_per_day)
        return Decimal(self.work_centre.available_hours_per_day)

    def calendar(self):
        """This machine's own days, on the bank's holiday region.

        The region is not overridable. A loom does not observe a
        different Diwali from the loom beside it, and a field that
        could say otherwise is a field somebody will eventually set by
        accident.
        """
        return WorkingCalendar(
            self.days_pattern(), self.work_centre.holiday_region
        )

    def rate_per_hour(self):
        """(rate, uom), from this machine or from the bank.

        Returns `(None, None)` when neither states one — the caller
        that needs a rate refuses for itself, with a message naming
        what it was trying to schedule. Raising here would make
        listing a bank's machines fail because one of them is
        unrated.
        """
        if self.capacity_per_hour is not None:
            return self.capacity_per_hour, self.capacity_uom
        return (
            self.work_centre.capacity_per_hour,
            self.work_centre.capacity_uom,
        )

    # -- what it can do -------------------------------------------------

    def minutes_on(self, day):
        """Minutes this machine has on one day, before anything is booked."""
        if not self.is_active:
            return ZERO
        if not self.calendar().is_working(day):
            return ZERO
        return self.hours_per_day() * MINUTES_PER_HOUR

    def minutes_available(self, start, end):
        """Minutes across a window, counting only the days it runs."""
        if end < start:
            raise ValidationError(
                f"A window from {start} to {end} runs backwards, and the "
                "hours it reports would too."
            )
        days = Decimal(self.calendar().count(start, end))
        if not self.is_active:
            return ZERO
        return days * self.hours_per_day() * MINUTES_PER_HOUR

    def check_in(self, work_centre):
        """Refuse a machine booked against a bank it does not belong to."""
        if work_centre is None:
            return
        if self.work_centre_id != work_centre.pk:
            raise ValidationError(
                f"{self} belongs to {self.work_centre.code} and this is "
                f"against {work_centre.code}. A run booked to a machine in "
                "another bank reads as capacity taken off a line that was "
                "standing idle."
            )


def machines_in(work_centre, active_only=True):
    rows = work_centre.machines.all()
    return list(rows.filter(is_active=True) if active_only else rows)
