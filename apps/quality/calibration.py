"""
The scales and testers the readings came off, and whether they could be
believed.

A bag weighed on a scale nobody has checked since last year is a number,
not a measurement. Every certificate, weight check and third-party
release in this plant rests on a handful of instruments: the platform
scale at conversion, the balance in the lab, the tensile tester.

**An instrument is calibrated on a day and due again on another.** On
any date it is in calibration (its latest standing calibration on or
before then passed or was adjusted, and is not yet due), overdue, out
of service (its latest calibration failed), or never calibrated.

**A reading may name the instrument that took it**, and one that does
is refused if the instrument was not in calibration on the day, was
retired, does not measure that characteristic, or read outside its
range. When the inspection posts, each such reading records the
calibration it relied on: a fact, not something recomputed. A
characteristic that says so demands an instrument on every reading.

**Found out of tolerance, it casts doubt backwards.** A calibration
that found the instrument out (adjusted, or failed) lists every
inspection measured on it since its last good calibration: those are
the batches whose weights nobody can now vouch for. That list is the
point of keeping a register at all.

**Voided** only while no reading has relied on it.
"""

import datetime

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, serialised, to_date


class Instrument(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    serial_number = models.CharField(max_length=64, blank=True)
    location = models.CharField(max_length=128, blank=True)
    interval_days = models.PositiveIntegerField(
        help_text="How long a calibration holds, unless the calibration says less.")
    measures = models.ManyToManyField(
        "quality.Characteristic", blank=True, related_name="instruments",
        help_text="What it may be used for. Blank, anything.")
    range_low = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    range_high = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True,
        help_text="What it was calibrated across. A reading outside it was never checked.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(check=Q(interval_days__gt=0),
                                   name="instrument_interval_positive"),
            models.CheckConstraint(
                check=Q(range_low__isnull=True) | Q(range_high__isnull=True)
                | Q(range_low__lt=models.F("range_high")),
                name="instrument_range_in_order"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def standing(self, on_date):
        """The latest standing calibration on or before a date, or None."""
        return self.calibrations.filter(
            posted=True, voided_at__isnull=True, calibrated_on__lte=to_date(on_date),
        ).order_by("-calibrated_on", "-id").first()

    def status(self, on_date):
        """(status, calibration): in calibration, overdue, out of service or never."""
        on_date = to_date(on_date)
        latest = self.standing(on_date)
        if latest is None:
            return "never calibrated", None
        if latest.result == CalibrationResult.FAIL:
            return "out of service", latest
        if on_date > latest.due_on:
            return "overdue", latest
        return "in calibration", latest


class CalibrationResult(models.TextChoices):
    PASS = "pass", "Found in tolerance"
    ADJUSTED = "adjusted", "Found out of tolerance and adjusted"
    FAIL = "fail", "Out of tolerance; taken out of service"


class Calibration(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT,
                                   related_name="calibrations")
    calibrated_on = models.DateField()
    due_on = models.DateField(
        null=True, blank=True,
        help_text="When it is due again. Left blank, the instrument's interval on.")
    result = models.CharField(max_length=10, choices=CalibrationResult.choices)
    performed_by = models.CharField(max_length=128,
                                    help_text="The lab or the person who did it.")
    certificate_reference = models.CharField(max_length=64, blank=True)
    traceable_to = models.CharField(max_length=128, blank=True,
                                    help_text="The reference standard, e.g. NABL weights.")
    notes = models.CharField(max_length=255, blank=True)
    posted = models.BooleanField(default=False, editable=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-calibrated_on", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""),
                                    name="calibration_number_unique"),
        ]

    def __str__(self):
        return self.number or f"Draft calibration of {self.instrument.code}"

    def save(self, *args, **kwargs):
        if self.pk and Calibration.objects.filter(pk=self.pk, posted=True).exists() \
                and not getattr(self, "_writing", False):
            raise ValidationError(f"{self} is posted. Void it and record another.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError(f"{self} is posted; void it.")
        return super().delete(*args, **kwargs)

    def _write(self, fields):
        self._writing = True
        try:
            self.save(update_fields=fields + ["updated_at"])
        finally:
            # Left set, the next save of this object would pass the guard too.
            self._writing = False

    @serialised("posted")
    def post(self):
        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        if not self.instrument.is_active:
            raise ValidationError(f"{self.instrument} is retired.")
        self.calibrated_on = to_date(self.calibrated_on)
        if self.calibrated_on > timezone.localdate():
            raise ValidationError("A calibration cannot be dated after today.")
        if not (self.performed_by or "").strip():
            raise ValidationError("Say who calibrated it.")
        if self.due_on is None:
            self.due_on = self.calibrated_on + datetime.timedelta(
                days=self.instrument.interval_days)
        self.due_on = to_date(self.due_on)
        if self.due_on <= self.calibrated_on:
            raise ValidationError("It falls due after the day it was calibrated.")
        self.number = DocumentSequence.next_for("quality.calibration", self.calibrated_on,
                                                name="Calibrations", prefix="CAL-")
        self.posted, self.posted_at = True, timezone.now()
        self._write(["number", "calibrated_on", "due_on", "posted", "posted_at"])

    @serialised("posted", "voided_at")
    def void(self, reason):
        from .models import Reading

        if not self.posted or self.voided_at is not None:
            raise ValidationError(f"{self} is not a standing calibration.")
        if not (reason or "").strip():
            raise ValidationError("Say why the calibration is withdrawn.")
        if Reading.objects.filter(calibration=self).exists():
            raise ValidationError(f"Readings were taken on the strength of {self}; it "
                                  "stands.")
        self.voided_at, self.voided_reason = timezone.now(), reason.strip()
        self._write(["voided_at", "voided_reason"])

    def found_out(self):
        return self.result in (CalibrationResult.ADJUSTED, CalibrationResult.FAIL)

    def suspect_inspections(self):
        """
        Inspections measured on this instrument since its last good
        calibration, where this one found it out of tolerance. Empty when
        it was found in tolerance.
        """
        from .models import Inspection

        if not self.found_out():
            return []
        before = self.instrument.calibrations.filter(
            posted=True, voided_at__isnull=True, calibrated_on__lt=self.calibrated_on,
            result__in=[CalibrationResult.PASS, CalibrationResult.ADJUSTED],
        ).order_by("-calibrated_on", "-id").first()
        found = Inspection.objects.filter(
            posted=True, voided_at__isnull=True, readings__instrument=self.instrument,
            inspected_on__lte=self.calibrated_on,
        )
        if before is not None:
            found = found.filter(inspected_on__gte=before.calibrated_on)
        return list(found.select_related("lot").distinct().order_by("inspected_on", "id"))


def check_readings(inspection):
    """
    As an inspection posts: each reading's instrument, and the calibration
    it relied on, frozen onto the reading.
    """
    from .models import Reading

    on_date = to_date(inspection.inspected_on)
    for reading in inspection.readings.select_related("plan_line__characteristic",
                                                      "instrument"):
        characteristic = reading.plan_line.characteristic
        instrument = reading.instrument
        if instrument is None:
            if characteristic.needs_calibrated_instrument:
                raise ValidationError(
                    f"{characteristic.code} is measured on a calibrated instrument; "
                    f"reading {reading.sample_reference or reading.pk} names none."
                )
            continue
        if not instrument.is_active:
            raise ValidationError(f"{instrument} is retired.")
        allowed = instrument.measures.all()
        if allowed and characteristic not in allowed:
            raise ValidationError(f"{instrument} does not measure {characteristic.code}.")
        if reading.value is not None and (
                (instrument.range_low is not None and reading.value < instrument.range_low)
                or (instrument.range_high is not None
                    and reading.value > instrument.range_high)):
            raise ValidationError(
                f"{reading.value.normalize():f} is outside what {instrument.code} was "
                "calibrated across."
            )
        status, calibration = instrument.status(on_date)
        if status != "in calibration":
            raise ValidationError(f"{instrument} was {status} on {on_date}.")
        # Inspection.post is atomic: a refusal at a later reading takes this
        # back, so no draft is left claiming a calibration it never used.
        Reading.objects.filter(pk=reading.pk).update(calibration=calibration)


def due(within_days=30, on_date=None):
    """Active instruments due, overdue, out of service or never calibrated, soonest first."""
    on_date = to_date(on_date) or timezone.localdate()
    horizon = on_date + datetime.timedelta(days=within_days)
    rows = []
    for instrument in Instrument.objects.filter(is_active=True):
        status, calibration = instrument.status(on_date)
        due_on = calibration.due_on if calibration is not None else None
        if status != "in calibration" or due_on <= horizon:
            rows.append({"instrument": instrument, "status": status, "due_on": due_on})
    return sorted(rows, key=lambda row: (row["due_on"] or datetime.date.min,
                                         row["instrument"].code))
