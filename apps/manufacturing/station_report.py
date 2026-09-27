"""
The 8 AM report: yesterday's shift-day at a loom exit station, and what
to act on today.

Read from what the station recorded, never re-judged: a roll's
exception is the one frozen when it was weighed, at the tolerance the
station held it to then.

**The tape balance.** What a contractor's looms had at the start of the
day, plus what was issued to them, less what was on them at the end, is
what they consumed. Fabric rolls and weighed waste account for most of
it; the rest is unaccounted. Counts are recorded against the shift-day
they close, so one day's closing count is the next day's opening count.
A day missing either cannot be balanced and says so: counting missing
tape as none would report a loss, or hide one, that nobody measured.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from .rolls import FabricRoll
from .station import LoomWaste, TapeCount

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


def _percent(part, whole):
    if not whole:
        return None
    return (part / whole * ONE_HUNDRED).quantize(Decimal("0.01"))


def _person(employee):
    return employee.employee_number if employee is not None else None


def tape_balance(station, contractor, shift_date, rolls):
    """One contractor's tape for one shift-day, or why it cannot be balanced."""
    from .orders import MaterialIssueLine

    opening = TapeCount.objects.filter(
        station=station, contractor=contractor,
        shift_date=shift_date - datetime.timedelta(days=1),
    ).first()
    closing = TapeCount.objects.filter(
        station=station, contractor=contractor, shift_date=shift_date
    ).first()
    issued = ZERO
    for line in MaterialIssueLine.objects.filter(
        issue__contractor=contractor, issue__issue_date=shift_date,
        issue__posted=True, issue__voided_at__isnull=True,
    ).select_related("issue", "item", "uom"):
        issued += line.stock_quantity() * line.issue.sign()
    returned = sum(
        (roll.net_weight_kg for roll in rolls if roll.machine.contractor_id == contractor.pk),
        ZERO,
    )
    waste = sum(
        (row.kg for row in LoomWaste.objects.filter(
            station=station, contractor=contractor, shift_date=shift_date)),
        ZERO,
    )
    row = {
        "contractor": contractor, "issued": issued, "rolls_kg": returned,
        "waste": waste, "opening": opening.kg if opening else None,
        "closing": closing.kg if closing else None,
        "missing": [name for name, count in (("opening", opening), ("closing", closing))
                    if count is None],
    }
    if row["missing"]:
        row.update(consumed=None, unaccounted=None, unaccounted_percent=None, over=False)
        return row
    consumed = opening.kg + issued - closing.kg
    unaccounted = consumed - returned - waste
    percent = _percent(unaccounted, consumed)
    row.update(
        consumed=consumed, unaccounted=unaccounted, unaccounted_percent=percent,
        over=percent is not None and percent > station.unaccounted_threshold_percent,
    )
    return row


def morning_report(station, shift_date):
    rolls = list(
        FabricRoll.objects.filter(station=station, shift_date=shift_date)
        .select_related("lot", "machine__contractor", "shift", "approved_by",
                        "weighed_by")
        .order_by("weighed_at", "id")
    )

    by_shift = defaultdict(int)
    for roll in rolls:
        by_shift[roll.shift.code] += 1

    looms = {}
    for roll in rolls:
        row = looms.setdefault(roll.machine_id, {
            "loom": roll.machine.code, "rolls": 0, "from_weight": ZERO, "declared": ZERO,
            "tolerance": roll.metres_tolerance_percent,
        })
        row["rolls"] += 1
        row["from_weight"] += roll.metres_from_weight
        row["declared"] += roll.length_m
        # The tolerance these rolls were held to, as frozen on them; the
        # latest if the station's limit changed during the day.
        row["tolerance"] = roll.metres_tolerance_percent
    loom_rows = []
    for row in sorted(looms.values(), key=lambda row: row["loom"]):
        row["variance_percent"] = _percent(row["declared"] - row["from_weight"],
                                           row["from_weight"])
        row["over"] = abs(row["variance_percent"]) > row["tolerance"]
        loom_rows.append(row)

    overrides = [
        {
            "roll": roll.lot.code,
            "reason": roll.get_override_reason_display(),
            "note": roll.override_note,
            "at": timezone.localtime(roll.weighed_at).strftime("%H:%M"),
            "shift": roll.shift.code,
            "net_kg": roll.net_weight_kg,
            "approved_by": _person(roll.approved_by),
        }
        for roll in rolls if roll.weight_source == "manual"
    ]

    contractors = {
        machine.contractor_id: machine.contractor
        for machine in station.machines.select_related("contractor")
        if machine.contractor_id
    }
    balances = [
        tape_balance(station, contractor, shift_date, rolls)
        for _, contractor in sorted(contractors.items(), key=lambda item: item[1].code)
    ]

    exceptions = []
    for row in sorted(loom_rows, key=lambda row: -abs(row["variance_percent"])):
        if row["over"]:
            direction = "above" if row["variance_percent"] > 0 else "below"
            exceptions.append({
                "kind": "metres",
                "title": f"{row['loom']}: declared metres "
                         f"{abs(row['variance_percent'])}% {direction} weight-derived",
                "detail": f"{row['rolls']} rolls. {row['declared']:,.0f} m declared against "
                          f"{row['from_weight']:,.0f} m from weight; limit ±{row['tolerance']}%.",
                "owner": "Production manager",
                "action": f"Measure two {row['loom']} rolls by length",
            })
    for balance in balances:
        name = balance["contractor"].name
        if balance["missing"]:
            exceptions.append({
                "kind": "tape_count",
                "title": f"{name}: tape cannot be balanced",
                "detail": f"No {' or '.join(balance['missing'])} count of tape on the looms.",
                "owner": "Plant system owner",
                "action": "Record the count of tape on the looms",
            })
        elif balance["over"]:
            exceptions.append({
                "kind": "unaccounted",
                "title": f"{name}: unaccounted tape {balance['unaccounted']:,.1f} kg, "
                         f"{balance['unaccounted_percent']}% of consumption",
                "detail": f"Threshold {station.unaccounted_threshold_percent}%. The closing "
                          "count is the least reliable figure in the balance.",
                "owner": "Plant system owner",
                "action": "Recount closing tape on the looms",
            })
    if overrides:
        exceptions.append({
            "kind": "overrides",
            "title": f"{len(overrides)} manual weight{'s' if len(overrides) > 1 else ''}",
            "detail": ", ".join(row["roll"] for row in overrides),
            "owner": "Plant system owner",
            "action": (f"Check scale {station.scale_code} before the next shift"
                       if station.scale_code else "Check the scale before the next shift"),
        })

    return {
        "station": station,
        "shift_date": shift_date,
        "rolls": len(rolls),
        "by_shift": dict(by_shift),
        "from_scale": sum(1 for roll in rolls if roll.weight_source == "scale"),
        "manual": len(overrides),
        "manual_percent": _percent(Decimal(len(overrides)), Decimal(len(rolls))),
        "looms": loom_rows,
        "overrides": overrides,
        "balances": balances,
        "exceptions": exceptions,
    }
