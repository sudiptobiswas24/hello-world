"""
How much of a run's material overrun its own measurements explain.

This is the join the whole build has been pointing at. A run ate a
hundred and sixty kilos of polymer more than the specification said,
and the specification also says the fabric should have been 87.5
grammes a square metre. If quality control weighed it at 91, the fabric
was four per cent heavy — and four per cent of the polymer is most of
the overrun, and the answer is not "the blend was wrong" but "the loom
was set too tight".

The two facts were always in the building and never in the same
sentence. The measurement lived in a quality file and the variance
lived in a closing journal entry, and a plant manager who wanted to put
them together did it on paper, at the end of the month, for the runs
somebody remembered.

**This is an explanation, not an attribution.** A first-order estimate:
the material that scales with the measured characteristic, times how
far the measurement was from target. It does not know that the crew
also ran the extruder hot, and it says how much it accounts for rather
than claiming the rest. A number that claimed the rest would be worse
than none, because somebody would stop looking.

The measurement is found by what the specification said it was for —
`derived_from` on the plan line — rather than by matching the
characteristic's name. Names get translated; that field does not.
"""

from decimal import Decimal


def output_lots(work_order):
    """The batches this run put on a shelf, main output and by-products."""
    lots = {}
    for entry in work_order.posted_entries():
        if entry.lot_id is not None:
            lots[entry.lot_id] = entry.lot
        for row in entry.byproducts.select_related("lot"):
            if row.lot_id is not None:
                lots[row.lot_id] = row.lot
    return list(lots.values())


def measured(work_order, source="gsm"):
    """
    What the run's own output was measured at, against what it should
    have been.

    Averaged across every standing inspection of every batch the run
    made: one roll read light and another heavy is one fabric, and the
    roll somebody happened to inspect first is not the answer.
    """
    from apps.quality.release import latest_inspection

    readings = []
    target = None
    for lot in output_lots(work_order):
        inspection = latest_inspection(lot)
        if inspection is None:
            continue
        for reading in inspection.readings.select_related("plan_line"):
            if reading.plan_line.derived_from != source:
                continue
            if reading.value is None:
                continue
            readings.append(reading.value)
            if target is None:
                target = reading.plan_line.target
    if not readings or not target:
        return None
    average = sum(readings, Decimal("0")) / len(readings)
    return {
        "source": source,
        "target": target,
        "measured": average,
        "readings": len(readings),
        "deviation": average - target,
        "deviation_percent": (average - target) / target * Decimal("100"),
    }


def explains(work_order, source="gsm"):
    """
    The part of the material variance the measurement accounts for.

    Measured against the planned material cost rather than against what
    was issued: the question is how much extra polymer a heavy fabric
    needs, and that is a property of the plan and the deviation, not of
    whatever else went wrong on the night.
    """
    reading = measured(work_order, source)
    if reading is None:
        return None
    planned = work_order.planned_material_cost or Decimal("0")
    accounted = planned * reading["deviation_percent"] / Decimal("100")
    unaccounted = work_order.unaccounted()
    # A run that came out light explains a saving, not an overrun, and
    # the share is then meaningless — dividing by a negative overrun
    # would report a confident percentage of the wrong thing.
    share = (
        accounted / unaccounted * Decimal("100")
        if unaccounted > 0 and accounted > 0 else None
    )
    return {
        **reading,
        "planned_material_cost": planned,
        "accounted_for": accounted,
        "unaccounted": unaccounted,
        "share_percent": share,
        "note": (
            "A first-order estimate: the material that scales with this "
            "measurement, times how far the measurement was from target. It "
            "does not claim the rest."
        ),
    }
