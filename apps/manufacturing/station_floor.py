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
- **Waste** collected at the machine — loom sweepings, tape-line lumps
  and purge, cutting trim — weighed and taken into stock as the waste
  the run's recipe gives back, against the run that made it. Until it
  is, the recipe's credit and the plan's regrind are figures nobody
  ever put on a shelf.

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


@transaction.atomic
def book_waste(station, operator, machine, kg, item_code="", at=None):
    """The waste off a machine's run, weighed into stock as the recipe names it."""
    return _give_back(station, operator, machine, kg, item_code, at, counted=False)


@transaction.atomic
def book_seconds(station, operator, machine, pieces, reason_code, at=None):
    """Off-grade sacks off a run, counted into stock as its seconds, with the defect."""
    from .scrap import ScrapReason
    from .station import run_on
    from .woven import BagSpecification

    reason = ScrapReason.objects.filter(code=reason_code, is_active=True).first()
    if reason is None:
        raise ValidationError(f"{reason_code} is not a defect in use.")
    run = run_on(machine)
    spec = BagSpecification.objects.filter(bom=run.bom).first()
    if spec is None or spec.seconds_item is None:
        raise ValidationError(f"{run} has no seconds item: its off-grade sacks are scrap.")
    return _give_back(station, operator, machine, pieces, spec.seconds_item.sku, at,
                      counted=True, note=f"Seconds, {reason.code}")


def _give_back(station, operator, machine, quantity, item_code, at, counted, note="Waste"):
    from .orders import ProductionByproduct, ProductionEntry
    from .station import run_on

    at = at or timezone.now()
    _shift, shift_date = _context(station, operator, machine, at)
    run = run_on(machine)
    kg = _number(quantity, "The seconds" if counted else "The waste")
    given_back = list(run.bom.byproducts.select_related("item__uom"))
    if item_code:
        rows = [row for row in given_back if row.item.sku == item_code]
    else:
        rows = given_back
    if not given_back:
        raise ValidationError(f"{run.bom} gives nothing back; its waste is not collected.")
    if len(rows) != 1:
        raise ValidationError(
            f"{run.bom} gives back {', '.join(r.item.sku for r in given_back)}; "
            "say which this is." if not item_code else
            f"{item_code} is not what {run.bom} gives back.")
    item = rows[0].item
    if not counted and item.uom.category != "weight":
        raise ValidationError(f"{item} is counted in {item.uom}; waste is weighed.")
    entry = ProductionEntry.objects.create(
        work_order=run, entry_date=shift_date, warehouse=station.warehouse,
        quantity_produced=ZERO, quantity_scrapped=ZERO, uom=run.uom,
        work_centre=machine.work_centre, machine=machine,
        memo=f"{note} {'counted' if counted else 'weighed'} at {station} by {operator}"[:255],
    )
    # Weighed in the item's own unit of weight.
    ProductionByproduct.objects.create(entry=entry, item=item, quantity=kg, uom=item.uom)
    entry.post()
    return entry


def waste_variance(order):
    """
    For each thing a run gives back: what its recipe expected for what
    the run has made so far, what was weighed, and the difference.
    """
    from .orders import ProductionByproduct

    made = order.quantity_produced() + order.quantity_scrapped()
    scale = order.bom.scale_for(made, order.uom) if made else ZERO
    rows = []
    for row in order.bom.byproducts.select_related("item", "uom"):
        expected = row.item.to_stock_quantity(row.quantity * scale, row.uom)
        weighed = sum(
            (line.item.to_stock_quantity(line.quantity, line.uom)
             for line in ProductionByproduct.objects.filter(
                 entry__work_order=order, entry__posted=True, entry__voided_at__isnull=True,
                 item=row.item)),
            ZERO)
        rows.append({"item": row.item, "expected": expected, "weighed": weighed,
                     "difference": weighed - expected})
    return rows


@transaction.atomic
def void_waste(entry, station, supervisor, operator, reason):
    if entry.warehouse_id != station.warehouse_id or entry.quantity_produced != 0 or \
            entry.quantity_scrapped != 0 or not entry.machine_id or \
            not station.machines.filter(pk=entry.machine_id).exists():
        raise ValidationError(f"{entry} is not waste weighed at {station}.")
    if not (reason or "").strip():
        raise ValidationError("Say why the waste is withdrawn.")
    _supervised(station, supervisor, operator)
    entry.void(memo=f"Withdrawn at {station}: {reason.strip()}"[:255])


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
            entry.quantity_scrapped <= 0 or not entry.machine_id or \
            not station.machines.filter(pk=entry.machine_id).exists():
        raise ValidationError(f"{entry} is not scrap booked at {station}.")
    if not (reason or "").strip():
        raise ValidationError("Say why the scrap is withdrawn.")
    _supervised(station, supervisor, operator)
    entry.void(memo=f"Withdrawn at {station}: {reason.strip()}"[:255])
