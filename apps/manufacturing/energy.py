"""
Electricity, read off the meters and laid on the runs that used it.

**Not posted.** The electricity bill reaches the ledger through
purchasing, and machine time is absorbed into work in progress at the
work centre's standard rate. A second charge from the meters would pay
the bill twice. What the meters add is the truth the standard is
checked against: how many kWh a run actually took, what they cost at
the tariff of the day, what a kilogramme of it took, and how much the
plant drew with nothing running.

**Consumption is derived, not stored.** A meter is read, cumulatively,
at the end of a shift; what a shift used is the reading less the one
before, times the meter's multiplier. A reading voided or entered late
changes its neighbours' consumption by itself, because nothing else
holds it. A shift nobody read is covered by the next reading, which then
spans both.

**Laid on runs by minutes booked.** The kWh between two readings go to
the time bookings on that machine (or, for a meter on a whole work
centre, any machine in it) inside the interval, in proportion to their
minutes. An interval with nothing booked is idle, and reported as such:
lights, compressors and looms left running. A booking the meters cannot
see — no machine named where the meter is on one, or after the last
reading — is counted as unmetered on the run rather than guessed at.

A machine metered on its own and through its work centre's meter would
have its shifts counted twice, so the two cannot overlap. A sub-meter
under a main meter is not modelled.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, serialised

from .quoting import DatedRate

ZERO = Decimal("0")
MINUTES_PER_HOUR = Decimal("60")


def slot(day, shift):
    """Where a shift-day sits in time. No shift is the whole day, so it comes last."""
    return (day, shift.starts_at if shift is not None else datetime.time.max)


class EnergyMeter(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    machine = models.ForeignKey("manufacturing.Machine", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="energy_meters")
    work_centre = models.ForeignKey("manufacturing.WorkCentre", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="energy_meters")
    multiplier = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal("1"),
        help_text="kWh per unit the dial moves. A meter behind current "
                  "transformers reads a fraction of what passes; its factor "
                  "is on the meter card.",
    )
    installed_on = models.DateField()
    initial_reading = models.DecimalField(max_digits=14, decimal_places=3,
                                          default=Decimal("0"))
    retired_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                check=(Q(machine__isnull=False) & Q(work_centre__isnull=True))
                | (Q(machine__isnull=True) & Q(work_centre__isnull=False)),
                name="energy_meter_serves_one_thing"),
            models.CheckConstraint(check=Q(multiplier__gt=0), name="energy_meter_multiplier_positive"),
            models.CheckConstraint(check=Q(initial_reading__gte=0),
                                   name="energy_meter_initial_not_negative"),
            models.CheckConstraint(
                check=Q(retired_on__isnull=True) | Q(retired_on__gte=models.F("installed_on")),
                name="energy_meter_retired_after_installed"),
        ]

    def __str__(self):
        return self.code

    def serves(self):
        return self.machine or self.work_centre

    def standing_readings(self):
        return sorted(self.readings.filter(voided_at__isnull=True).select_related("shift"),
                      key=lambda reading: slot(reading.shift_date, reading.shift))

    def save(self, *args, **kwargs):
        # The constraints again, in words: the API's serializer does not
        # run check constraints, and a database error is no answer.
        if bool(self.machine_id) == bool(self.work_centre_id):
            raise ValidationError("A meter is on one machine or on one work centre.")
        if self.multiplier is None or self.multiplier <= 0:
            raise ValidationError("A meter's multiplier is more than nothing.")
        if self.retired_on and self.retired_on < self.installed_on:
            raise ValidationError("A meter cannot be retired before it was installed.")
        if not self._state.adding:
            before = EnergyMeter.objects.get(pk=self.pk)
            if self.readings.exists():
                for name in ("multiplier", "initial_reading", "installed_on",
                             "machine_id", "work_centre_id"):
                    if getattr(before, name) != getattr(self, name):
                        raise ValidationError(
                            f"{self} has been read; its {name.replace('_id', '')} is what "
                            "those readings were taken against. Retire it and install "
                            "another."
                        )
            last = self.standing_readings()[-1:] if self.retired_on else []
            if last and last[0].shift_date > self.retired_on:
                raise ValidationError(
                    f"{self} was read on {last[0].shift_date}; it cannot have been "
                    f"retired on {self.retired_on}."
                )
        self._refuse_overlap()
        super().save(*args, **kwargs)

    def _refuse_overlap(self):
        from .machines import Machine

        if self.machine_id:
            clash = Q(machine_id=self.machine_id) | Q(
                work_centre_id=Machine.objects.get(pk=self.machine_id).work_centre_id)
        else:
            clash = Q(work_centre_id=self.work_centre_id) | Q(
                machine__work_centre_id=self.work_centre_id)
        others = EnergyMeter.objects.filter(clash).exclude(pk=self.pk).filter(
            Q(retired_on__isnull=True) | Q(retired_on__gte=self.installed_on))
        if self.retired_on:
            others = others.filter(installed_on__lte=self.retired_on)
        other = others.first()
        if other is not None:
            raise ValidationError(
                f"{other} already meters {other.serves()} from {other.installed_on}; two "
                "meters over the same machine count its shifts twice."
            )

    def delete(self, *args, **kwargs):
        if self.readings.exists():
            raise ValidationError(f"{self} has been read; retire it instead.")
        return super().delete(*args, **kwargs)


class MeterReading(AuditModel):
    meter = models.ForeignKey(EnergyMeter, on_delete=models.PROTECT, related_name="readings")
    shift_date = models.DateField()
    shift = models.ForeignKey("manufacturing.Shift", null=True, blank=True,
                              on_delete=models.PROTECT, related_name="+")
    reading = models.DecimalField(max_digits=14, decimal_places=3)
    read_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["meter", "-shift_date"]
        constraints = [
            models.UniqueConstraint(fields=["meter", "shift_date", "shift"],
                                    condition=Q(voided_at__isnull=True),
                                    name="one_standing_reading_a_shift"),
        ]

    def __str__(self):
        return f"{self.meter} {self.reading} at the end of {self.shift or 'the day'} {self.shift_date}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            if not getattr(self, "_voiding", False):
                raise ValidationError("A reading is what the dial said. Void it and read again.")
            return super().save(*args, **kwargs)
        meter = self.meter
        if self.shift_date < meter.installed_on or (
                meter.retired_on and self.shift_date > meter.retired_on):
            raise ValidationError(
                f"{meter} served from {meter.installed_on}"
                + (f" to {meter.retired_on}" if meter.retired_on else "")
                + f"; it was not there on {self.shift_date}."
            )
        here = slot(self.shift_date, self.shift)
        before, after = meter.initial_reading, None
        for other in meter.standing_readings():
            there = slot(other.shift_date, other.shift)
            if there == here:
                raise ValidationError(f"{meter} is already read for that shift: {other.reading}.")
            if there < here:
                before = other.reading
            elif after is None:
                after = other.reading
        if self.reading < before:
            raise ValidationError(
                f"{meter} read {before} before this; a meter does not run backwards. "
                "A replaced meter is a new meter."
            )
        if after is not None and self.reading > after:
            raise ValidationError(f"{meter} read {after} after this; this cannot be more.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A reading is what the dial said. Void it.")

    @serialised("voided_at")
    def void(self, reason):
        if self.voided_at is not None:
            raise ValidationError(f"{self} is already void.")
        if not (reason or "").strip():
            raise ValidationError("Say why the reading is withdrawn.")
        self.voided_at, self.voided_reason = timezone.now(), reason.strip()
        self._voiding = True
        try:
            self.save(update_fields=["voided_at", "voided_reason", "updated_at"])
        finally:
            # Left set, the next save of this object would pass the guard too.
            self._voiding = False


class EnergyTariff(DatedRate):
    rate = models.DecimalField(max_digits=10, decimal_places=4, help_text="Per kWh.")

    class Meta:
        ordering = ["-valid_from"]
        constraints = [
            models.UniqueConstraint(fields=["valid_from"], name="one_energy_tariff_a_day"),
            models.CheckConstraint(check=Q(rate__gte=0), name="energy_tariff_not_negative"),
        ]

    def __str__(self):
        return f"{self.rate} a kWh from {self.valid_from}"


def intervals(meter):
    """(after, up to and including, kWh, the reading that closed it), in order."""
    previous, value = (meter.installed_on, datetime.time.min), meter.initial_reading
    for reading in meter.standing_readings():
        here = slot(reading.shift_date, reading.shift)
        yield previous, here, (reading.reading - value) * meter.multiplier, reading
        previous, value = here, reading.reading


def allocation(meter):
    """([(booking, kWh)], [(closing reading, kWh)]): each interval laid on its bookings, or idle."""
    from .orders import TimeBooking

    spans = list(intervals(meter))
    if not spans:
        return [], []
    bookings = TimeBooking.objects.filter(
        posted=True, voided_at__isnull=True,
        booking_date__gte=meter.installed_on, booking_date__lte=spans[-1][1][0],
    ).select_related("shift")
    if meter.machine_id:
        bookings = bookings.filter(machine_id=meter.machine_id)
    else:
        bookings = bookings.filter(operation__work_centre_id=meter.work_centre_id)
    bookings = list(bookings)
    shares, idle = [], []
    for after, upto, kwh, reading in spans:
        inside = [booking for booking in bookings
                  if after < slot(booking.booking_date, booking.shift) <= upto]
        minutes = sum((booking.minutes for booking in inside), ZERO)
        if not minutes:
            idle.append((reading, kwh))
            continue
        shares.extend((booking, kwh * booking.minutes / minutes) for booking in inside)
    return shares, idle


def _tariff(day):
    found = EnergyTariff.in_force(day)
    return None if found is None else found.rate


def run_energy(work_order):
    """What a run drew, from the meters: kWh, cost, per unit made, and against standard."""
    bookings = list(work_order.posted_time().select_related("operation__work_centre", "machine"))
    machines = {booking.machine_id for booking in bookings if booking.machine_id}
    centres = {booking.operation.work_centre_id for booking in bookings}
    meters = EnergyMeter.objects.filter(Q(machine_id__in=machines) | Q(work_centre_id__in=centres))
    kwh, cost, metered, unpriced = ZERO, ZERO, {}, set()
    for meter in meters:
        for booking, share in allocation(meter)[0]:
            if booking.work_order_id != work_order.pk:
                continue
            metered[booking.pk] = booking
            kwh += share
            rate = _tariff(booking.booking_date)
            if rate is None:
                unpriced.add(booking.booking_date)
            else:
                cost += share * rate
    standard, unstandard = ZERO, 0
    by_pk = {booking.pk: booking for booking in bookings}
    for pk in metered:
        booking = by_pk[pk]
        per_hour = booking.operation.work_centre.standard_kwh_per_hour
        if per_hour is None:
            unstandard += 1
        else:
            standard += booking.minutes / MINUTES_PER_HOUR * per_hour
    made = work_order.quantity_produced()
    warnings = []
    if unpriced:
        warnings.append("No tariff in force on " + ", ".join(sorted(map(str, unpriced)))
                        + "; the cost is not known.")
    if unstandard:
        warnings.append(f"{unstandard} metered booking(s) are on a work centre with no "
                        "standard kWh an hour; the variance leaves them out.")
    return {
        "kwh": kwh,
        "cost": None if unpriced else cost,
        "kwh_per_unit": kwh / made if made else None,
        "standard_kwh": standard if not unstandard else None,
        "variance_kwh": kwh - standard if not unstandard else None,
        "metered_minutes": sum((booking.minutes for booking in metered.values()), ZERO),
        "unmetered_minutes": sum((booking.minutes for booking in bookings
                                  if booking.pk not in metered), ZERO),
        "warnings": warnings,
    }


def idle_energy(start, end):
    """What was drawn with nothing booked, closed by a reading in [start, end]."""
    rows = []
    for meter in EnergyMeter.objects.select_related("machine", "work_centre"):
        for reading, kwh in allocation(meter)[1]:
            if start <= reading.shift_date <= end:
                rate = _tariff(reading.shift_date)
                rows.append({"meter": meter.code, "serves": str(meter.serves()),
                             "shift_date": reading.shift_date,
                             "shift": reading.shift.code if reading.shift_id else None,
                             "kwh": kwh, "cost": None if rate is None else kwh * rate})
    return rows


def metered_total(meter):
    """Everything the meter recorded, for footing against what was laid out."""
    return sum((kwh for _, _, kwh, _ in intervals(meter)), ZERO)


def by_run(meter):
    runs = defaultdict(lambda: ZERO)
    for booking, share in allocation(meter)[0]:
        runs[booking.work_order_id] += share
    return dict(runs)
