"""
Hours a machine is not available, planned before they happen.

`Downtime` records a stoppage after the fact and `DowntimeReason`
already knows whether it was planned. Neither takes an hour out of the
machine in advance, so a plan scheduled a run straight through a
service the plant had every intention of doing.

**Two clocks, and a machine needs both.** A gearbox is serviced every
ninety days whether or not it ran; a loom's shuttle is changed every
five hundred running hours whether that takes a month or a quarter.
A schedule may state either or both, and it falls due on whichever
comes first — which is what a maintenance department actually does and
what a single "every N days" field cannot express.

**Hours run are derived from the time bookings**, never counted into a
field. The bookings are already there, they are already the basis of
overall equipment effectiveness, and a second running total would be
wrong the first time a booking was voided.

**A job is what takes the capacity, not the schedule.** The schedule
says how often; a job is one dated occurrence of it, and only a job
that has not been done yet is booked against the machine. That keeps
the load book reading facts — a service on the fourteenth — rather
than inferring dates from a rule every time somebody asks.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel, to_date

ZERO = Decimal("0")
MINUTES_PER_HOUR = Decimal("60")


class MaintenanceSchedule(AuditModel):
    """How often a machine needs attention, and for how long."""

    work_centre = models.ForeignKey(
        "WorkCentre", on_delete=models.CASCADE, related_name="maintenance"
    )
    name = models.CharField(max_length=255)
    every_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Calendar days between services. A gearbox is done every "
                  "ninety days whether or not the machine ran.",
    )
    every_run_hours = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Running hours between services, read off the time "
                  "bookings. A shuttle is changed every five hundred hours "
                  "whether that takes a month or a quarter.",
    )
    duration_minutes = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="How long the machine is down for it. This is what comes "
                  "out of the plan's capacity.",
    )
    last_done_on = models.DateField(
        null=True, blank=True,
        help_text="When it was last done. Empty means never, and the first "
                  "one is due now.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["work_centre", "name"]
        constraints = [
            models.CheckConstraint(
                check=Q(duration_minutes__gt=0),
                name="maintenance_takes_some_time",
            ),
            models.CheckConstraint(
                check=Q(every_days__isnull=False) | Q(every_run_hours__isnull=False),
                name="maintenance_has_a_clock",
            ),
            models.CheckConstraint(
                check=Q(every_days__isnull=True) | Q(every_days__gt=0),
                name="maintenance_days_positive",
            ),
            models.CheckConstraint(
                check=Q(every_run_hours__isnull=True) | Q(every_run_hours__gt=0),
                name="maintenance_run_hours_positive",
            ),
        ]

    def __str__(self):
        return f"{self.name} on {self.work_centre.code}"

    def hours_run_since(self, on_date=None):
        """
        Machine hours booked since it was last done.

        Off the time bookings, which already exist and are already
        what effectiveness is computed from. A running total in a
        field of its own would be wrong the first time a booking was
        voided.
        """
        from .orders import TimeBooking

        # Through the operation, which is what a booking actually names,
        # and voided bookings excluded: a booking that was taken back
        # did not put hours on the machine, and counting it would have
        # a service fall due on time the loom never ran.
        bookings = TimeBooking.objects.filter(
            operation__work_centre=self.work_centre, posted=True,
            voided_at__isnull=True,
        )
        if self.last_done_on:
            bookings = bookings.filter(booking_date__gt=self.last_done_on)
        if on_date:
            bookings = bookings.filter(booking_date__lte=to_date(on_date))
        minutes = bookings.aggregate(total=Sum("minutes"))["total"] or ZERO
        return minutes / MINUTES_PER_HOUR

    def due_on(self, as_of=None):
        """
        The calendar date this is next due, or None when it is only
        counted in running hours.
        """
        if self.every_days is None:
            return None
        as_of = to_date(as_of) or timezone.now().date()
        if self.last_done_on is None:
            return as_of
        return self.last_done_on + datetime.timedelta(days=self.every_days)

    def hours_remaining(self, as_of=None):
        """Running hours left, or None when it is only counted in days."""
        if self.every_run_hours is None:
            return None
        return self.every_run_hours - self.hours_run_since(as_of)

    def is_due(self, as_of=None):
        """
        Due on whichever clock runs out first.

        Both are checked because a machine that has been standing
        still for six months still needs its gearbox done, and one
        that has run flat out for six weeks needs its shuttle changed
        early.
        """
        as_of = to_date(as_of) or timezone.now().date()
        by_date = self.due_on(as_of)
        if by_date is not None and by_date <= as_of:
            return True
        left = self.hours_remaining(as_of)
        return left is not None and left <= 0

    @transaction.atomic
    def raise_job(self, due_on=None, as_of=None):
        """
        Put a dated occurrence on the board.

        Refused when one is already open, because two open jobs for
        one schedule take the machine out twice and a planner cannot
        tell which is real.
        """
        open_already = self.jobs.filter(done_on__isnull=True).first()
        if open_already is not None:
            raise ValidationError(
                f"{open_already} is already on the board for this schedule. "
                "Two open jobs would take the machine out twice."
            )
        as_of = to_date(as_of) or timezone.now().date()
        when = to_date(due_on) or self.due_on(as_of) or as_of
        return MaintenanceJob.objects.create(
            schedule=self, work_centre=self.work_centre, due_on=when,
            planned_minutes=self.duration_minutes,
        )


class MaintenanceJob(AuditModel):
    """
    One dated occurrence of a service, which is what takes the hours.

    Only an open job is booked against the machine. A job already done
    is history, and one still on the board is an hour the plan may not
    schedule a run into.
    """

    schedule = models.ForeignKey(
        MaintenanceSchedule, null=True, blank=True, on_delete=models.CASCADE,
        related_name="jobs",
        help_text="Empty on a one-off — a breakdown repair booked in, which "
                  "no schedule predicted.",
    )
    work_centre = models.ForeignKey(
        "WorkCentre", on_delete=models.PROTECT, related_name="maintenance_jobs"
    )
    due_on = models.DateField()
    planned_minutes = models.DecimalField(max_digits=10, decimal_places=2)
    done_on = models.DateField(null=True, blank=True, editable=False)
    actual_minutes = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, editable=False,
        help_text="What it really took, recorded at completion.",
    )
    downtime = models.ForeignKey(
        "Downtime", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="maintenance", editable=False,
        help_text="The stoppage this became once it happened, so the hours "
                  "appear in effectiveness alongside every other stoppage.",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["due_on", "work_centre", "id"]
        constraints = [
            models.CheckConstraint(
                check=Q(planned_minutes__gt=0),
                name="maintenance_job_takes_some_time",
            ),
        ]

    def __str__(self):
        label = self.schedule.name if self.schedule_id else "Maintenance"
        return f"{label} on {self.work_centre.code}, {self.due_on}"

    def is_open(self):
        return self.done_on is None

    @transaction.atomic
    def complete(self, on_date=None, minutes=None, reason=None, shift=None):
        """
        Record that it happened, and put the hours where every other
        stoppage goes.

        A `Downtime` row rather than a private total, so that a
        service shows up in overall equipment effectiveness beside a
        breakdown and a changeover. Planned downtime is still
        downtime: a machine being serviced is a machine not weaving.
        """
        if not self.is_open():
            raise ValidationError(f"{self} was already done on {self.done_on}.")
        from .shifts import Downtime, DowntimeReason

        on_date = to_date(on_date) or timezone.now().date()
        minutes = Decimal(minutes) if minutes is not None else self.planned_minutes
        if minutes <= 0:
            raise ValidationError(
                "A service that took no time is a service nobody did."
            )
        if reason is None:
            reason, _made = DowntimeReason.objects.get_or_create(
                code="PM", defaults={
                    "name": "Planned maintenance", "is_planned": True,
                },
            )
        self.downtime = Downtime.objects.create(
            work_centre=self.work_centre, shift_date=on_date, shift=shift,
            reason=reason, minutes=minutes,
            notes=str(self)[:255],
        )
        self.done_on = on_date
        self.actual_minutes = minutes
        self.save(update_fields=[
            "done_on", "actual_minutes", "downtime", "updated_at",
        ])
        if self.schedule_id:
            self.schedule.last_done_on = on_date
            self.schedule.save(update_fields=["last_done_on", "updated_at"])
        return self.downtime


def due_now(as_of=None, work_centre=None):
    """
    Schedules that have run out on either clock and have no job on the
    board.

    The list a maintenance department works from, and the one a
    planner wants before promising a machine for a fortnight.
    """
    schedules = MaintenanceSchedule.objects.filter(is_active=True)
    if work_centre is not None:
        schedules = schedules.filter(work_centre=work_centre)
    found = []
    for schedule in schedules.select_related("work_centre"):
        if schedule.jobs.filter(done_on__isnull=True).exists():
            continue
        if schedule.is_due(as_of):
            found.append(schedule)
    return found
