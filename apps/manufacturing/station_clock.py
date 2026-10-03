"""
Clocking a machine on and off a run at the station.

Time bookings were typed in afterwards: four hours on loom seventeen,
from memory. At the machine, the operator starts its clock when the run
starts and stops it when it stops, and the booking writes itself.

**The machine's clock, not a person's.** Machine time is what a booking
charges; two operators each clocking the same loom would book, and
cost, its hours twice. So one clock runs per machine; the first
operator starts it, others join it, and everybody on it is named on the
bookings (for attribution — the labour in the cost comes from the
machine's rate, not from them).

**Split at the shift change.** A clock running from four in the
afternoon to ten at night is two shifts' work, and booked as one it
puts the night crew's hours on the day. It is stopped as one booking
per shift it crossed.

**A clock left running** past a day is somebody who forgot. Stopping it
takes a supervisor's PIN, so a day of machine hours nobody saw is not
booked by default.

Bookings a clock made are withdrawn together, with a supervisor's PIN.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel

LEFT_RUNNING = datetime.timedelta(hours=24)


class MachineClock(AuditModel):
    station = models.ForeignKey("manufacturing.LoomStation", on_delete=models.PROTECT,
                                related_name="clocks")
    machine = models.ForeignKey("manufacturing.Machine", on_delete=models.PROTECT,
                                related_name="clocks")
    operation = models.ForeignKey("manufacturing.WorkOrderOperation", on_delete=models.PROTECT,
                                  related_name="clocks")
    started_at = models.DateTimeField()
    started_by = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    crew = models.ManyToManyField("hr.Employee", related_name="+")
    stopped_at = models.DateTimeField(null=True, blank=True, editable=False)
    stopped_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+", editable=False)
    bookings = models.ManyToManyField("manufacturing.TimeBooking", blank=True,
                                      related_name="clocks")
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(fields=["machine"], condition=Q(stopped_at__isnull=True),
                                    name="one_running_clock_per_machine"),
        ]

    def __str__(self):
        return f"{self.machine.code} on {self.operation} from {self.started_at:%Y-%m-%d %H:%M}"


def _open(machine):
    return MachineClock.objects.select_for_update().filter(
        machine=machine, stopped_at__isnull=True).first()


def _shift_end(moment):
    from .shifts import Shift

    shift = Shift.covering(moment)
    if shift is None:
        raise ValidationError(f"No shift runs at {timezone.localtime(moment):%H:%M}.")
    begun = timezone.make_aware(datetime.datetime.combine(shift.shift_date_for(moment),
                                                          shift.starts_at))
    return begun + datetime.timedelta(hours=float(shift.hours))


@transaction.atomic
def start_clock(station, operator, machine, at=None):
    from .station_floor import _context, _step

    at = at or timezone.now()
    _context(station, operator, machine, at)
    running = _open(machine)
    if running is not None:
        raise ValidationError(f"{machine.code}'s clock has been running since "
                              f"{timezone.localtime(running.started_at):%H:%M}; join it.")
    _run, step = _step(machine)
    clock = MachineClock.objects.create(station=station, machine=machine, operation=step,
                                        started_at=at, started_by=operator)
    clock.crew.add(operator)
    return clock


@transaction.atomic
def join_clock(station, operator, machine, at=None):
    from .station_floor import _context

    at = at or timezone.now()
    _context(station, operator, machine, at)
    running = _open(machine)
    if running is None:
        raise ValidationError(f"{machine.code}'s clock is not running; start it.")
    running.crew.add(operator)
    return running


@transaction.atomic
def stop_clock(station, operator, machine, quantity=None, supervisor=None, at=None):
    from .orders import TimeBooking
    from .station import check_supervisor
    from .station_floor import _context, _number

    at = at or timezone.now()
    _context(station, operator, machine, at)
    running = _open(machine)
    if running is None:
        raise ValidationError(f"{machine.code}'s clock is not running.")
    if at <= running.started_at:
        raise ValidationError("It stops after it started.")
    if at - running.started_at > LEFT_RUNNING:
        check_supervisor(station, supervisor, operator)
    completed = _number(quantity, "What was made") if quantity not in (None, "") else None
    crew = list(running.crew.all())
    bookings, begun = [], running.started_at
    while begun < at:
        ended = min(at, _shift_end(begun))
        minutes = Decimal((ended - begun).total_seconds() / 60).quantize(Decimal("0.01"))
        if minutes > 0:
            booking = TimeBooking(
                work_order=running.operation.work_order, operation=running.operation,
                booking_date=timezone.localtime(begun).date(), started_at=begun,
                machine=machine, minutes=minutes,
                memo=f"Clocked at {station}"[:255],
            )
            booking.save()
            booking.operators.set(crew)
            bookings.append(booking)
        begun = ended
    if completed is not None:
        TimeBooking.objects.filter(pk=bookings[-1].pk).update(quantity_completed=completed)
    for booking in bookings:
        booking.refresh_from_db()
        booking.post()
    running.stopped_at, running.stopped_by = at, operator
    running.save(update_fields=["stopped_at", "stopped_by", "updated_at"])
    running.bookings.set(bookings)
    return running


@transaction.atomic
def void_clock(clock, station, supervisor, operator, reason):
    from .station import check_supervisor

    if clock.station_id != station.pk:
        raise ValidationError(f"{clock} was not clocked at {station}.")
    if clock.stopped_at is None or clock.voided_at is not None:
        raise ValidationError(f"{clock} is not a stopped, standing clock.")
    if not (reason or "").strip():
        raise ValidationError("Say why the time is withdrawn.")
    check_supervisor(station, supervisor, operator)
    for booking in clock.bookings.all():
        booking.void(memo=f"Withdrawn at {station}: {reason.strip()}"[:255])
    clock.voided_at, clock.voided_reason = timezone.now(), reason.strip()
    clock.save(update_fields=["voided_at", "voided_reason", "updated_at"])
