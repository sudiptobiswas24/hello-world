"""
What an operator books at a station besides weights: a machine
stopped, a step's count, and output spoiled, each with its reason.

The figures these feed already exist — downtime and its reasons for
overall equipment effectiveness, step counts and reasoned scrap for the
run's flow — but only the office could enter them, the next morning,
from a paper sheet. Booked at the machine by the person standing at it,
signed in by PIN, on the shift that covers the moment:

- **A stoppage** on a machine this station serves, for so many minutes,
  for a reason; against the run on the machine where there is exactly
  one (a changeover between two belongs to neither).
- **A count** of what the machine's step of its run has made good. The
  run's last step is its output and is booked as such, not counted.
- **Scrap** off the machine's step, for a reason: a production entry of
  nothing good and that much spoiled, so it is costed and written off
  like any other scrap, and the flow shows where it happened.

A figure booked wrong is withdrawn with a supervisor's PIN (somebody
else, who approves here) and booked again: stoppages are kept and no
longer counted, counts are voided, scrap entries reversed.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

ZERO = Decimal("0")


def _number(value, what):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"{what} is a number.")
    if not number.is_finite() or number <= 0:
        raise ValidationError(f"{what} is more than nothing.")
    return number


def _context(station, operator, machine, at):
    from .shifts import Shift

    if not station.is_active:
        raise ValidationError(f"{station} is not in use.")
    if operator is None:
        raise ValidationError("Nobody is signed in at this station.")
    if not station.machines.filter(pk=machine.pk).exists():
        raise ValidationError(f"{station} does not serve {machine.code}.")
    shift = Shift.covering(at)
    if shift is None:
        raise ValidationError(f"No shift runs at {timezone.localtime(at):%H:%M}.")
    shift_date = shift.shift_date_for(at)
    if not operator.is_working_on(shift_date):
        raise ValidationError(f"{operator} does not work here on {shift_date}.")
    return shift, shift_date


def _step(machine):
    """The run on this machine and its step on this machine's bank."""
    from .station import run_on

    run = run_on(machine)
    steps = list(run.operations.filter(work_centre=machine.work_centre).order_by("sequence"))
    named = [step for step in steps if step.machine_id == machine.pk]
    steps = named or steps
    if len(steps) != 1:
        raise ValidationError(f"{run} passes {machine.work_centre.code} more than once; "
                              "book it in the office against the step.")
    return run, steps[0]


@transaction.atomic
def book_stoppage(station, operator, machine, reason_code, minutes, notes="", at=None):
    from .shifts import Downtime, DowntimeReason
    from .station import run_on

    at = at or timezone.now()
    shift, shift_date = _context(station, operator, machine, at)
    reason = DowntimeReason.objects.filter(code=reason_code, is_active=True).first()
    if reason is None:
        raise ValidationError(f"{reason_code} is not a stoppage reason in use.")
    try:
        run = run_on(machine)
    except ValidationError:
        # Nothing on it, or two runs: a stoppage then belongs to no run.
        run = None
    return Downtime.objects.create(
        work_centre=machine.work_centre, machine=machine, shift_date=shift_date, shift=shift,
        reason=reason, minutes=_number(minutes, "Minutes stopped"), work_order=run,
        notes=(notes or "")[:255], station=station, booked_by=operator,
    )


@transaction.atomic
def count_step(station, operator, machine, quantity, at=None):
    from .scrap import report

    at = at or timezone.now()
    _shift, shift_date = _context(station, operator, machine, at)
    _run, step = _step(machine)
    return report(step, _number(quantity, "The count"), on_date=shift_date, machine=machine,
                  memo=f"Counted at {station} by {operator}")


@transaction.atomic
def book_scrap(station, operator, machine, reason_code, quantity, at=None):
    from .orders import ProductionEntry
    from .scrap import ProductionScrap, ScrapReason

    at = at or timezone.now()
    _shift, shift_date = _context(station, operator, machine, at)
    reason = ScrapReason.objects.filter(code=reason_code, is_active=True).first()
    if reason is None:
        raise ValidationError(f"{reason_code} is not a scrap reason in use.")
    run, step = _step(machine)
    quantity = _number(quantity, "Scrap")
    entry = ProductionEntry.objects.create(
        work_order=run, entry_date=shift_date, warehouse=station.warehouse,
        quantity_produced=ZERO, quantity_scrapped=quantity, uom=run.uom,
        work_centre=machine.work_centre, machine=machine,
        memo=f"Scrap booked at {station} by {operator}"[:255],
    )
    ProductionScrap.objects.create(entry=entry, reason=reason, operation=step,
                                   quantity=quantity)
    entry.post()
    return entry


def _supervised(station, supervisor, operator):
    from .station import check_supervisor

    check_supervisor(station, supervisor, operator)


@transaction.atomic
def void_stoppage(stoppage, station, supervisor, operator, reason):
    if stoppage.station_id != station.pk:
        raise ValidationError(f"{stoppage} was not booked at {station}.")
    _supervised(station, supervisor, operator)
    stoppage.void(reason, by=supervisor)


@transaction.atomic
def void_count(counted, station, supervisor, operator, reason):
    if counted.machine_id is None or not station.machines.filter(
            pk=counted.machine_id).exists():
        raise ValidationError(f"{counted} was not counted at {station}.")
    _supervised(station, supervisor, operator)
    counted.void(reason)


@transaction.atomic
def void_scrap(entry, station, supervisor, operator, reason):
    if entry.warehouse_id != station.warehouse_id or entry.quantity_produced != 0 or \
            not entry.machine_id or not station.machines.filter(pk=entry.machine_id).exists():
        raise ValidationError(f"{entry} is not scrap booked at {station}.")
    if not (reason or "").strip():
        raise ValidationError("Say why the scrap is withdrawn.")
    _supervised(station, supervisor, operator)
    entry.void(memo=f"Withdrawn at {station}: {reason.strip()}"[:255])
