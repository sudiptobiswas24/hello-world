"""
Who was on the machine, and what happened while they were.

A run in this plant lasts days and passes through six or nine crews. A
material variance of a hundred and forty kilos is a fact about the run;
which shift it happened on is the fact somebody can act on, and until a
booking says which, the number sits there being true and useless.

**The night shift is the part that is easy to get wrong.** It starts at
ten and ends at six, so half its hours fall on the following calendar
day. A yield report grouped by the clock puts those hours on the wrong
day, splits one crew's work across two rows, and produces two shifts
that both look wrong. So a booking carries the shift DATE — the day the
shift is named for, not the day the clock said — and the conversion is
here, in one place, rather than in each report.

Downtime is the other half. A loom that ran four hours out of eight was
not a slow loom, it was a stopped one, and the two have different
answers. With run time, downtime, the routing's rate and what the run
actually made, the three ratios that multiply into overall equipment
effectiveness are all derivable:

    availability = ran / (ran + stopped)
    performance  = what the rate says those hours should have made
                   against what they did
    quality      = good output against everything that came off

None of the three is stored. Each is replayed from the bookings, the
downtime and the production entries that produced it, for the same
reason nothing else here is stored: a copy drifts the moment one of
them is corrected.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, to_date

MINUTES_PER_HOUR = Decimal("60")


class Shift(AuditModel):
    """
    One named slot in the plant's day.

    A plant runs the same two or three of these for years, so they are
    rows rather than a pattern generated from a rule: the night shift
    that started at ten until March and at nine after it is two facts,
    and a rule would only ever hold the second.
    """

    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=64)
    starts_at = models.TimeField(
        help_text="Clock time the shift begins. Wall clock, not a duration "
                  "from midnight: this is the number stuck on the noticeboard."
    )
    hours = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("8"),
        help_text="How long it runs. Eight for three shifts, twelve for two.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["starts_at", "code"]
        constraints = [
            models.CheckConstraint(
                check=Q(hours__gt=0) & Q(hours__lte=24),
                name="shift_hours_in_a_day",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def ends_at(self):
        """The clock time it finishes, which may be the next morning."""
        start = datetime.datetime.combine(datetime.date(2000, 1, 1), self.starts_at)
        return (start + datetime.timedelta(hours=float(self.hours))).time()

    def crosses_midnight(self):
        return self.ends_at() <= self.starts_at

    def minutes(self):
        return self.hours * MINUTES_PER_HOUR

    def shift_date_for(self, moment):
        """
        Which shift-day a wall-clock moment belongs to.

        Two in the morning on the twenty-second is the night shift of
        the twenty-FIRST, and every yield report that groups by the
        calendar date instead puts half a crew's work on the wrong day
        and makes two shifts look wrong at once. The conversion lives
        here so that no report has to know it.
        """
        if timezone.is_aware(moment):
            moment = timezone.localtime(moment)
        if self.crosses_midnight() and moment.time() < self.starts_at:
            return (moment - datetime.timedelta(days=1)).date()
        return moment.date()

    def covers(self, moment):
        """Whether a wall-clock moment falls inside this shift."""
        if timezone.is_aware(moment):
            moment = timezone.localtime(moment)
        clock = moment.time()
        if self.crosses_midnight():
            return clock >= self.starts_at or clock < self.ends_at()
        return self.starts_at <= clock < self.ends_at()

    @classmethod
    def covering(cls, moment):
        """The active shift a moment falls in, or None."""
        for shift in cls.objects.filter(is_active=True):
            if shift.covers(moment):
                return shift
        return None

    def overlaps(self, other):
        """Whether two shifts both claim any part of the clock."""
        for probe in (self, other):
            reference = other if probe is self else self
            for moment in (probe.starts_at, probe.ends_at()):
                point = datetime.datetime.combine(datetime.date(2000, 1, 2), moment)
                if moment == probe.ends_at():
                    # The end is exclusive; step back a minute to ask
                    # about the last minute the shift actually holds.
                    point -= datetime.timedelta(minutes=1)
                if reference.covers(point) and probe.covers(point):
                    return True
        return False

    def save(self, *args, **kwargs):
        if self.hours and Decimal(self.hours) > 24:
            raise ValidationError(f"{self.code} cannot run longer than a day.")
        if self.is_active:
            others = Shift.objects.filter(is_active=True)
            if self.pk:
                others = others.exclude(pk=self.pk)
            for other in others:
                if self.overlaps(other):
                    raise ValidationError(
                        f"{self.code} runs {self.starts_at}–{self.ends_at()} and "
                        f"{other.code} runs {other.starts_at}–{other.ends_at()}; "
                        "they claim the same hours. Which crew a booking lands "
                        "on would then depend on the order the rows come back "
                        "in, which is not an answer."
                    )
        super().save(*args, **kwargs)


class DowntimeReason(AuditModel):
    """
    Why a machine was not running.

    Planned and unplanned are different questions with different owners:
    a changeover is the sales mix, a warp break is maintenance, and
    netting them into one "downtime" figure means neither gets an
    answer. Availability counts both — the loom was stopped either way —
    but the split is what anybody does about it.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    is_planned = models.BooleanField(
        default=False,
        help_text="A changeover, a scheduled service, a shutdown somebody "
                  "chose. A warp break is not planned; a colour change is.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Downtime(AuditModel):
    """
    A machine stopped, for this long, for this reason.

    Against the machine and the shift rather than against a run: a loom
    with a broken warp is not making anything, so there is no run to
    book it to, and booking it to whatever ran last is how a shift's
    stoppage lands on the wrong order.
    """

    number = models.CharField(max_length=32, blank=True)
    work_centre = models.ForeignKey(
        "WorkCentre", on_delete=models.PROTECT, related_name="downtime"
    )
    shift_date = models.DateField(
        help_text="The day the shift is named for. A stoppage at two in the "
                  "morning belongs to the night shift of the day before."
    )
    shift = models.ForeignKey(
        Shift, null=True, blank=True, on_delete=models.PROTECT,
        related_name="downtime",
    )
    reason = models.ForeignKey(
        DowntimeReason, on_delete=models.PROTECT, related_name="downtime"
    )
    minutes = models.DecimalField(max_digits=10, decimal_places=2)
    work_order = models.ForeignKey(
        "WorkOrder", null=True, blank=True, on_delete=models.PROTECT,
        related_name="downtime",
        help_text="The run that was on the machine, where there was one. A "
                  "changeover between two runs belongs to neither.",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-shift_date", "work_centre", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""),
                name="downtime_number_unique",
            ),
            models.CheckConstraint(
                check=Q(minutes__gt=0), name="downtime_minutes_positive"
            ),
        ]

    def __str__(self):
        return (
            f"{self.work_centre.code} {self.shift_date} "
            f"{self.reason.code} {self.minutes}m"
        )

    def hours(self):
        return self.minutes / MINUTES_PER_HOUR

    def save(self, *args, **kwargs):
        self.shift_date = to_date(self.shift_date)
        if self.shift_id is not None:
            # Asked of everything already recorded for this machine on
            # this shift, not of this row alone: two stoppages of three
            # hundred minutes each pass one at a time and between them
            # make an eight-hour shift ten hours long. That had the loom
            # reading nought per cent available because somebody entered
            # the same breakdown twice.
            already = Downtime.objects.filter(
                work_centre=self.work_centre, shift_date=self.shift_date,
                shift=self.shift,
            )
            if self.pk:
                already = already.exclude(pk=self.pk)
            total = sum(
                (row.minutes for row in already), Decimal("0")
            ) + self.minutes
            if total > self.shift.minutes():
                raise ValidationError(
                    f"{self.shift} runs {self.shift.minutes()} minutes and "
                    f"{self.work_centre} would be down for {total} of them on "
                    f"{self.shift_date}. A machine cannot be stopped for longer "
                    "than the shift it was stopped in."
                )
        if self.work_order_id is not None:
            covers = self.work_order.operations.filter(
                work_centre=self.work_centre
            ).exists()
            if self.work_order.routing_id is not None and not covers:
                raise ValidationError(
                    f"{self.work_order} never goes near "
                    f"{self.work_centre}; a stoppage there is not its."
                )
        if not self.number:
            self.number = DocumentSequence.next_for(
                "manufacturing.downtime", self.shift_date,
                name="Downtime", prefix="DT-",
            )
        super().save(*args, **kwargs)
