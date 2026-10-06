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

**A breakdown is raised on the stoppage it was.** The operator books
the stoppage at the machine; maintenance raises a job on it, says what
failed, books its fitters' time, and closes it with the cause and what
was done. The stoppage is already the downtime, so closing the job
adds none — a second row would count the same hour twice in
effectiveness. And the stoppage cannot be withdrawn from under a job
that rests on it.

**Spares are issued to a job**, while it is open: a write-down of the
store under the maintenance reason, so they leave stock at their cost
and land in maintenance expense, and are returned by voiding the issue
— exactly what was taken comes back. A job is not cancelled with spares
standing against it: they were taken for something, or they go back.

**Reliability is derived**: failures from the breakdown jobs, running
hours from the time bookings, repair time from the stoppages. Mean time
between failures is running hours per failure; mean time to repair is
the stoppage minutes of the repaired ones. No failures is no figure,
not infinity.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel, serialised, to_date

ZERO = Decimal("0")
MINUTES_PER_HOUR = Decimal("60")
CENTS = Decimal("0.01")
SPARE_ISSUE = "maintenance spare issue"


class MaintenanceSchedule(AuditModel):
    """How often a machine needs attention, and for how long."""

    work_centre = models.ForeignKey(
        "WorkCentre", on_delete=models.CASCADE, related_name="maintenance"
    )
    machine = models.ForeignKey(
        "manufacturing.Machine", null=True, blank=True,
        on_delete=models.PROTECT, related_name="maintenance_schedules",
        help_text="Which machine this service is for. Blank services the "
                  "bank as a whole, which is right for a compressor feeding "
                  "twelve looms and wrong for the looms: a beam change on "
                  "loom seventeen falls due on loom seventeen's hours, not "
                  "on the shed's.",
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
        where = self.machine.code if self.machine_id else self.work_centre.code
        return f"{self.name} on {where}"

    def clean(self):
        self._check_machine()

    def save(self, *args, **kwargs):
        self._check_machine()
        super().save(*args, **kwargs)

    def _check_machine(self):
        if self.machine_id is not None and self.work_centre_id is not None:
            self.machine.check_in(self.work_centre)

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
        if self.machine_id is not None:
            # One machine's hours, not the bank's. A twelve-loom shed
            # runs twelve hours of bookings for every hour any one loom
            # turns, so a beam change rated at five hundred hours would
            # fall due every forty — and a service that cries wolf
            # twelve times too often is a service nobody does.
            bookings = bookings.filter(machine=self.machine)
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
        as_of = to_date(as_of) or timezone.localdate()
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
        as_of = to_date(as_of) or timezone.localdate()
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
        open_already = self.jobs.open().first()
        if open_already is not None:
            raise ValidationError(
                f"{open_already} is already on the board for this schedule. "
                "Two open jobs would take the machine out twice."
            )
        as_of = to_date(as_of) or timezone.localdate()
        when = to_date(due_on) or self.due_on(as_of) or as_of
        return MaintenanceJob.objects.create(
            schedule=self, work_centre=self.work_centre,
            machine=self.machine, due_on=when,
            planned_minutes=self.duration_minutes,
        )


class MaintenanceJobQuerySet(models.QuerySet):
    def open(self):
        """On the board: not done and not cancelled. What takes capacity."""
        return self.filter(done_on__isnull=True, cancelled_at__isnull=True)


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
    machine = models.ForeignKey(
        "manufacturing.Machine", null=True, blank=True,
        on_delete=models.PROTECT, related_name="maintenance_jobs",
        help_text="Which machine goes out. Blank takes the bank, which is "
                  "what a shared compressor does and what a beam change "
                  "must not.",
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
    is_breakdown = models.BooleanField(default=False, editable=False)
    fault = models.CharField(max_length=255, blank=True,
                             help_text="What failed, as reported.")
    technician = models.ForeignKey("hr.Employee", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    cause = models.CharField(max_length=255, blank=True, editable=False)
    action_taken = models.CharField(max_length=255, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_reason = models.CharField(max_length=255, blank=True, editable=False)

    objects = MaintenanceJobQuerySet.as_manager()

    class Meta:
        ordering = ["due_on", "work_centre", "id"]
        constraints = [
            models.CheckConstraint(
                check=Q(planned_minutes__gt=0),
                name="maintenance_job_takes_some_time",
            ),
            models.CheckConstraint(
                check=Q(is_breakdown=False) | (Q(downtime__isnull=False)
                                               & Q(schedule__isnull=True)),
                name="breakdown_rests_on_a_stoppage",
            ),
            models.UniqueConstraint(
                fields=["downtime"],
                condition=Q(downtime__isnull=False, cancelled_at__isnull=True),
                name="one_standing_job_per_stoppage",
            ),
        ]

    def __str__(self):
        label = self.schedule.name if self.schedule_id else "Maintenance"
        where = self.machine.code if self.machine_id else self.work_centre.code
        return f"{label} on {where}, {self.due_on}"

    def clean(self):
        self._check_machine()

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_closing", False):
            stored = type(self).objects.filter(pk=self.pk).values(
                "done_on", "cancelled_at").first()
            if stored and stored["done_on"] is not None:
                raise ValidationError(f"{self} is done; it is as it was done.")
            if stored and stored["cancelled_at"] is not None:
                raise ValidationError(f"{self} was cancelled.")
        self._check_machine()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.done_on is not None:
            raise ValidationError(f"{self} is done; it is as it was done.")
        if self.is_breakdown or self.labour.exists() or self.spares.exists():
            raise ValidationError(f"{self} is a record of a failure and of work; cancel it.")
        return super().delete(*args, **kwargs)

    def _check_machine(self):
        if self.machine_id is not None and self.work_centre_id is not None:
            self.machine.check_in(self.work_centre)

    def is_open(self):
        return self.done_on is None and self.cancelled_at is None

    def spares_value(self):
        """What the spares still standing against it cost, from the issues' own entries."""
        return sum((issue.value() for issue in self.spares.filter(
            adjustment__voided_at__isnull=True).select_related("adjustment")), ZERO)

    def labour_minutes(self):
        total = self.labour.aggregate(total=Sum("minutes"))["total"] or ZERO
        return Decimal(total).quantize(CENTS)

    def _close(self, fields):
        self._closing = True
        try:
            self.save(update_fields=[*fields, "updated_at"])
        finally:
            self._closing = False

    @serialised("cancelled_at", "done_on")
    def cancel(self, reason):
        """Raised in error, or no longer wanted: off the board, and kept."""
        if self.cancelled_at is not None:
            raise ValidationError(f"{self} was cancelled.")
        if self.done_on is not None:
            raise ValidationError(f"{self} was already done on {self.done_on}.")
        reason = " ".join((reason or "").split())
        if not reason:
            raise ValidationError("Say why the job is cancelled.")
        if self.spares.filter(adjustment__voided_at__isnull=True).exists():
            raise ValidationError(f"{self} has spares issued to it; return them first.")
        self.cancelled_at, self.cancelled_reason = timezone.now(), reason[:255]
        self._close(["cancelled_at", "cancelled_reason"])

    @serialised("cancelled_at", "done_on")
    def complete(self, on_date=None, minutes=None, reason=None, shift=None, cause="",
                 action=""):
        """
        Record that it happened, and put the hours where every other
        stoppage goes.

        A `Downtime` row rather than a private total, so that a
        service shows up in overall equipment effectiveness beside a
        breakdown and a changeover. Planned downtime is still
        downtime: a machine being serviced is a machine not weaving.

        A breakdown already has its stoppage — it was raised on it — so
        it is closed on that one, with what caused it and what was done.
        """
        if self.cancelled_at is not None:
            raise ValidationError(f"{self} was cancelled.")
        if not self.is_open():
            raise ValidationError(f"{self} was already done on {self.done_on}.")
        from .shifts import Downtime, DowntimeReason

        on_date = to_date(on_date) or timezone.localdate()
        if self.is_breakdown:
            action = " ".join((action or "").split())
            if not action:
                raise ValidationError("Say what was done to put it right.")
            if minutes is not None:
                raise ValidationError(
                    f"The time {self.machine or self.work_centre} was down is the "
                    f"stoppage's, {self.downtime.minutes} minutes; correct the stoppage.")
            self.done_on, self.actual_minutes = on_date, self.downtime.minutes
            self.cause, self.action_taken = " ".join((cause or "").split())[:255], action[:255]
            self._close(["done_on", "actual_minutes", "cause", "action_taken"])
            return self.downtime
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
            work_centre=self.work_centre, machine=self.machine,
            shift_date=on_date, shift=shift,
            reason=reason, minutes=minutes,
            notes=str(self)[:255],
        )
        self.done_on = on_date
        self.actual_minutes = minutes
        self._close(["done_on", "actual_minutes", "downtime"])
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
        if schedule.jobs.open().exists():
            continue
        if schedule.is_due(as_of):
            found.append(schedule)
    return found


class MaintenanceLabour(AuditModel):
    """A fitter's time on a job, while it is open."""

    job = models.ForeignKey(MaintenanceJob, on_delete=models.PROTECT, related_name="labour")
    technician = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    worked_on = models.DateField()
    minutes = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["job", "worked_on", "id"]
        constraints = [
            models.CheckConstraint(check=Q(minutes__gt=0), name="maintenance_labour_positive"),
        ]

    def __str__(self):
        return f"{self.technician} {self.minutes} min on {self.job}"

    def _check_open(self):
        job = MaintenanceJob.objects.get(pk=self.job_id)
        if job.done_on is not None:
            raise ValidationError(f"{job} is done; its labour is as booked.")
        if job.cancelled_at is not None:
            raise ValidationError(f"{job} was cancelled.")

    def save(self, *args, **kwargs):
        self._check_open()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._check_open()
        return super().delete(*args, **kwargs)


@transaction.atomic
def raise_breakdown(downtime, fault, technician=None, planned_minutes=None):
    """A job on a stoppage that was a failure."""
    from .shifts import Downtime

    downtime = Downtime.objects.select_for_update().get(pk=downtime.pk)
    if downtime.voided_at is not None:
        raise ValidationError(f"{downtime} was withdrawn; there is nothing to repair.")
    if downtime.reason.is_planned:
        raise ValidationError(f"{downtime.reason} is planned; a breakdown is not.")
    fault = " ".join((fault or "").split())
    if not fault:
        raise ValidationError("Say what failed.")
    standing = MaintenanceJob.objects.filter(downtime=downtime,
                                             cancelled_at__isnull=True).first()
    if standing is not None:
        raise ValidationError(f"{standing} is already raised on {downtime}.")
    minutes = Decimal(str(planned_minutes)) if planned_minutes is not None else downtime.minutes
    if minutes <= 0:
        raise ValidationError("A repair takes some time.")
    return MaintenanceJob.objects.create(
        is_breakdown=True, work_centre=downtime.work_centre, machine=downtime.machine,
        due_on=downtime.shift_date, planned_minutes=minutes, downtime=downtime,
        fault=fault[:255], technician=technician,
    )


def reliability(start, end, machine=None, work_centre=None):
    """
    Failures, mean time between them and mean time to repair, between
    two dates, for a machine or a bank.
    """
    from .orders import TimeBooking

    start, end = to_date(start), to_date(end)
    jobs = MaintenanceJob.objects.filter(
        is_breakdown=True, cancelled_at__isnull=True,
        downtime__shift_date__gte=start, downtime__shift_date__lte=end,
    ).select_related("downtime")
    bookings = TimeBooking.objects.filter(posted=True, voided_at__isnull=True,
                                          booking_date__gte=start, booking_date__lte=end)
    if machine is not None:
        jobs = jobs.filter(machine=machine)
        bookings = bookings.filter(machine=machine)
    elif work_centre is not None:
        jobs = jobs.filter(work_centre=work_centre)
        bookings = bookings.filter(operation__work_centre=work_centre)
    else:
        raise ValidationError("Say which machine or which bank.")
    jobs = list(jobs)
    repaired = [job for job in jobs if job.done_on is not None]
    run_hours = (bookings.aggregate(total=Sum("minutes"))["total"] or ZERO) / MINUTES_PER_HOUR
    repair_minutes = sum((job.downtime.minutes for job in repaired), ZERO)
    labour = MaintenanceLabour.objects.filter(job__in=jobs).aggregate(
        total=Sum("minutes"))["total"] or ZERO
    spares = sum((job.spares_value() for job in jobs), ZERO)
    return {
        "start": start, "end": end,
        "failures": len(jobs),
        "repaired": len(repaired),
        "run_hours": Decimal(run_hours).quantize(CENTS),
        "mtbf_hours": (run_hours / len(jobs)).quantize(CENTS) if jobs else None,
        "mttr_minutes": (repair_minutes / len(repaired)).quantize(CENTS) if repaired else None,
        "labour_minutes": Decimal(labour).quantize(CENTS),
        "spares_value": spares,
    }


class SpareIssue(AuditModel):
    """Spare parts out of the store and onto a job, as one stock write-down."""

    job = models.ForeignKey(MaintenanceJob, on_delete=models.PROTECT, related_name="spares")
    adjustment = models.OneToOneField("inventory.StockAdjustment", on_delete=models.PROTECT,
                                      related_name="spare_issue")
    issued_to = models.ForeignKey("hr.Employee", null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["job", "id"]

    def __str__(self):
        return f"{self.adjustment} to {self.job}"

    def value(self):
        """What left the store, positive."""
        return -self.adjustment.total_value()

    def is_standing(self):
        return self.adjustment.voided_at is None


@transaction.atomic
def issue_spares(job, warehouse, lines, on_date=None, issued_to=None, reason=None):
    """
    `lines` as [(item, quantity)] or [(item, quantity, lot)], in the
    item's own unit. Written down under the spares reason.
    """
    from apps.inventory.adjustments import StockAdjustment, StockAdjustmentLine

    from .orders import ManufacturingSettings

    job = MaintenanceJob.objects.select_for_update().get(pk=job.pk)
    if not job.is_open():
        raise ValidationError(f"{job} is not open; spares go to a job being worked on.")
    reason = reason or ManufacturingSettings.get().spares_reason
    if reason is None:
        raise ValidationError("Say which adjustment reason spares are written off under, "
                              "in the manufacturing settings.")
    from .positions import on_the_jobs_machine, place

    rows = []
    for row in lines or []:
        item, quantity, lot, position = (tuple(row) + (None, None))[:4]
        if position is not None:
            on_the_jobs_machine(job, position)
        try:
            quantity = Decimal(str(quantity))
        except (ArithmeticError, TypeError, ValueError):
            raise ValidationError(f"The quantity of {item} is a number.")
        if not quantity.is_finite() or quantity <= 0:
            raise ValidationError(f"Issue more than nothing of {item}.")
        rows.append((item, quantity, lot, position))
    if not rows:
        raise ValidationError("Say which spares are issued.")
    adjustment = StockAdjustment.objects.create(
        adjustment_date=to_date(on_date) or timezone.localdate(), warehouse=warehouse,
        reason=reason, memo=f"Spares for {job}"[:255], raised_by=SPARE_ISSUE,
    )
    for item, quantity, lot, _ in rows:
        StockAdjustmentLine.objects.create(adjustment=adjustment, item=item, uom=item.uom,
                                           quantity=-quantity, lot=lot)
    adjustment.post()
    issue = SpareIssue.objects.create(job=job, adjustment=adjustment, issued_to=issued_to)
    place(issue, [(item, quantity, position) for item, quantity, _, position in rows])
    return issue


@transaction.atomic
def return_spares(issue, on_date=None):
    """What was issued comes back whole, while the job is still open."""
    if not issue.is_standing():
        raise ValidationError(f"{issue} was already returned.")
    if not issue.job.is_open():
        raise ValidationError(f"{issue.job} is closed; its spares are as they were used. "
                              "Put anything left back with a stock adjustment.")
    issue.adjustment.void(on_date=on_date, memo=f"Spares returned from {issue.job}"[:255],
                          through=SPARE_ISSUE)
    # Back on the shelf, so out of the positions they were said to have gone to.
    issue.placements.all().delete()
    return issue
