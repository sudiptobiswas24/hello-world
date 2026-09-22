"""
Overall equipment effectiveness, replayed rather than stored.

Three ratios, and the point of them is that they multiply: a line at
90% on each is at 73% overall, which is the number that surprises
people. Each answers a different question and each has a different
owner.

    availability  did it run?          ran / (ran + stopped)
    performance   did it run at speed? the minutes the output should
                                       have taken against the minutes
                                       it did
    quality       was the output any good?  good / everything made

**Performance is measured in minutes, not in units.** The obvious
version — what it made against what it should have made — cannot be
summed across a window in which a line ran two products, because a kilo
of tape and a sack are not addable. Turning each booking into "the time
this output should have needed" makes every term minutes, and minutes
add.

**Quality refuses to add across units.** A work centre that made both
kilogrammes and pieces has no single quality figure, and the plausible
wrong one — adding them — is worse than none. The per-item breakdown is
always there; the overall figure appears only when everything that came
off the machine is counted in the same kind of thing. In this plant
that is always true, because an extruder makes tape and a cutting line
makes sacks; the restriction is there for the day it is not.

Nothing here is stored. Every figure is replayed from the bookings, the
downtime and the production entries that produced it, so a corrected
booking corrects the report and cannot leave a stale copy behind.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

MINUTES_PER_HOUR = Decimal("60")


class _Unshifted:
    """
    The hours nobody said whose they were.

    A distinct answer from "any shift": without it the by-shift rows
    silently fail to add up to the window they were cut from, and a
    reader who checks is left wondering which figure is wrong.
    """

    def __str__(self):
        return "Unattributed"


UNSHIFTED = _Unshifted()


def _for_shift(rows, shift):
    if shift is None:
        return rows
    if shift is UNSHIFTED:
        return rows.filter(shift__isnull=True)
    return rows.filter(shift=shift)


def _in_bom_units(booking, quantity):
    """
    A booking's completed quantity in the unit its rate is quoted in.

    `units_per_hour` was frozen against the bill of materials' own unit
    at release; `quantity_completed` is written in the run's. They are
    usually the same and the day they are not is the day this matters.
    """
    order = booking.operation.work_order
    if order.uom_id == order.bom.uom_id:
        return quantity
    return order.uom.convert_to(quantity, order.bom.uom)


def _bookings(work_centre, start, end, shift=None):
    from .orders import TimeBooking

    rows = TimeBooking.objects.filter(
        operation__work_centre=work_centre,
        posted=True, voided_at__isnull=True,
        booking_date__gte=start, booking_date__lte=end,
    ).select_related(
        "operation", "operation__work_order", "operation__work_order__uom",
        "operation__work_order__bom", "operation__work_order__bom__uom",
    )
    return _for_shift(rows, shift)


def _downtime(work_centre, start, end, shift=None):
    from .shifts import Downtime

    rows = Downtime.objects.filter(
        work_centre=work_centre,
        shift_date__gte=start, shift_date__lte=end,
    ).select_related("reason")
    return _for_shift(rows, shift)


def _entries(work_centre, start, end):
    from .orders import ProductionEntry

    return ProductionEntry.objects.filter(
        work_centre=work_centre, posted=True, voided_at__isnull=True,
        entry_date__gte=start, entry_date__lte=end,
    ).select_related("work_order", "work_order__item", "uom")


def _check_window(start, end):
    if end < start:
        raise ValidationError(
            f"A window from {start} to {end} runs backwards. It would report "
            "nought hours and nought stoppages, which reads exactly like a "
            "machine that stood idle all week."
        )


def availability(work_centre, start, end, shift=None):
    """
    Did it run. Planned and unplanned stoppage both count — the loom was
    stopped either way — but they are reported apart, because a
    changeover is the sales mix and a warp break is maintenance.
    """
    _check_window(start, end)
    ran = sum(
        (row.minutes for row in _bookings(work_centre, start, end, shift)),
        Decimal("0"),
    )
    stopped = Decimal("0")
    planned = Decimal("0")
    for row in _downtime(work_centre, start, end, shift):
        stopped += row.minutes
        if row.reason.is_planned:
            planned += row.minutes
    total = ran + stopped
    return {
        "ran_minutes": ran,
        "stopped_minutes": stopped,
        "planned_stop_minutes": planned,
        "unplanned_stop_minutes": stopped - planned,
        "ratio": (ran / total) if total else None,
    }


def performance(work_centre, start, end, shift=None):
    """
    Did it run at speed.

    Only bookings somebody counted contribute. A shift that ran four
    hours and did not say what came off it has told the truth about the
    four hours and nothing about the speed, and putting it in the
    denominator would read its silence as zero output.
    """
    _check_window(start, end)
    ideal = Decimal("0")
    actual = Decimal("0")
    counted = 0
    for booking in _bookings(work_centre, start, end, shift):
        if booking.quantity_completed is None:
            continue
        rate = booking.operation.units_per_hour
        if not rate:
            continue
        made = _in_bom_units(booking, booking.quantity_completed)
        ideal += made / rate * MINUTES_PER_HOUR
        actual += booking.minutes
        counted += 1
    return {
        "counted_bookings": counted,
        "ideal_minutes": ideal,
        "actual_minutes": actual,
        "ratio": (ideal / actual) if actual else None,
    }


def quality(work_centre, start, end):
    """
    Was the output any good.

    Per item always; overall only when everything that came off the
    machine is counted in the same kind of thing. Adding kilogrammes to
    pieces would give a number, and the number would mean nothing.
    """
    _check_window(start, end)
    per_item = {}
    roots = set()
    for entry in _entries(work_centre, start, end):
        item = entry.work_order.item
        row = per_item.setdefault(
            item.pk, {"item": item, "good": Decimal("0"), "scrapped": Decimal("0")}
        )
        row["good"] += entry.stock_quantity()
        row["scrapped"] += entry.scrapped_stock_quantity()
        roots.add(item.uom.root().pk)
    for row in per_item.values():
        total = row["good"] + row["scrapped"]
        row["ratio"] = (row["good"] / total) if total else None
    good = sum((row["good"] for row in per_item.values()), Decimal("0"))
    scrapped = sum((row["scrapped"] for row in per_item.values()), Decimal("0"))
    total = good + scrapped
    comparable = len(roots) <= 1
    return {
        "by_item": list(per_item.values()),
        "good": good if comparable else None,
        "scrapped": scrapped if comparable else None,
        "ratio": (good / total) if comparable and total else None,
        "note": None if comparable else (
            "This machine made things counted in different units in this "
            "window, so there is no one quality figure for it. The per-item "
            "rows are the answer."
        ),
    }


def effectiveness(work_centre, start, end, shift=None):
    """
    The three together, and their product where all three exist.

    Missing rather than assumed: a window in which nobody counted the
    output has no performance figure, and a plant that reads a missing
    ratio as one would congratulate itself on a line it never measured.
    """
    up = availability(work_centre, start, end, shift)
    speed = performance(work_centre, start, end, shift)
    good = quality(work_centre, start, end)
    ratios = [up["ratio"], speed["ratio"], good["ratio"]]
    overall = None
    if all(ratio is not None for ratio in ratios):
        overall = ratios[0] * ratios[1] * ratios[2]
    missing = [
        name for name, ratio in zip(
            ("availability", "performance", "quality"), ratios
        ) if ratio is None
    ]
    return {
        "work_centre": work_centre,
        "start": start,
        "end": end,
        "shift": shift,
        "availability": up,
        "performance": speed,
        "quality": good,
        "oee": overall,
        "unmeasured": missing,
    }


def by_shift(work_centre, start, end):
    """
    A row per crew's slot, which is the grouping a plant manager reads.

    Every active shift appears, including the ones that booked nothing:
    a night shift with no hours against it is the most interesting row
    on the page.

    And last, the hours nobody attributed. Without that row the rows do
    not add up to the window they were cut from, and a reader who
    checks — which is the reader worth having — is left wondering which
    of the figures is lying.
    """
    from .shifts import Shift

    rows = [
        effectiveness(work_centre, start, end, shift=shift)
        for shift in Shift.objects.filter(is_active=True)
    ]
    loose = effectiveness(work_centre, start, end, shift=UNSHIFTED)
    if (
        loose["availability"]["ran_minutes"]
        or loose["availability"]["stopped_minutes"]
    ):
        rows.append(loose)
    return rows


def by_operator(start, end, work_centre=None):
    """
    What each person's hours produced.

    Attribution, not appraisal — a crew on the oldest loom in the shed
    will read worse than one on the newest, and the figure is only
    useful next to which machine it was on. So the machine is in every
    row.
    """
    from .orders import TimeBooking

    rows = TimeBooking.objects.filter(
        posted=True, voided_at__isnull=True,
        booking_date__gte=start, booking_date__lte=end,
    ).select_related(
        "operation", "operation__work_centre", "operation__work_order",
        "operation__work_order__uom", "operation__work_order__bom",
        "operation__work_order__bom__uom",
    ).prefetch_related("operators")
    if work_centre is not None:
        rows = rows.filter(operation__work_centre=work_centre)
    people = {}
    for booking in rows:
        crew = list(booking.operators.all())
        if not crew:
            continue
        # A booking with three people on it is three people's hours, not
        # a third of one machine's: the machine ran once and each of
        # them was there for all of it.
        for operator in crew:
            key = (operator.pk, booking.operation.work_centre_id)
            row = people.setdefault(key, {
                "operator": operator,
                "work_centre": booking.operation.work_centre,
                "minutes": Decimal("0"),
                "ideal_minutes": Decimal("0"),
                "bookings": 0,
            })
            row["minutes"] += booking.minutes
            row["bookings"] += 1
            if booking.quantity_completed is not None and booking.operation.units_per_hour:
                made = _in_bom_units(booking, booking.quantity_completed)
                row["ideal_minutes"] += (
                    made / booking.operation.units_per_hour * MINUTES_PER_HOUR
                )
    for row in people.values():
        row["performance"] = (
            row["ideal_minutes"] / row["minutes"] if row["minutes"] else None
        )
    return sorted(people.values(), key=lambda row: -row["minutes"])
