"""
A batch weighed off a line and gauged against its specification.

The tape line's doff and the blown-film line's roll are the same event:
a gross weight (the scale's, or typed with a supervisor's approval), a
tare off it, a handful of gauge readings — denier, micron — against the
one line of the specification's plan they answer, a supervisor and a
reason for a batch outside its limits, and the output booked into stock
as a numbered batch. Written once here so a fix to one is a fix to both.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

ZERO = Decimal("0")


def weigh_gross(station, operator, supervisor, gross_kg, source, at, typed_reason="",
                typed_note=""):
    """(gross, scale reading): the scale's weight, or a typed one somebody approved."""
    from .station_floor import _number
    from .station_scale import check_typed_weight, gross_weight

    if source == "manual":
        check_typed_weight(station, operator, supervisor, typed_reason, typed_note)
    elif source != "scale":
        raise ValidationError(f"{source!r} is not a weight source.")
    gross, reading = gross_weight(station, gross_kg, source, at)
    return _number(gross, "The gross weight"), reading


def typed_fields(source, supervisor, typed_reason, typed_note):
    """What a weighed row records of where its weight came from."""
    manual = source == "manual"
    return {"weight_source": source, "weight_approved_by": supervisor if manual else None,
            "typed_reason": typed_reason if manual else "",
            "typed_note": (typed_note or "").strip() if manual else ""}


def gauged_batch(station, operator, machine, order, plan, gauge, readings, noun, supervisor,
                 reason, shift_date, code, net, memo, instrument=None, prefix="D"):
    """
    Readings against the plan's `gauge` line, then the batch, its
    inspection and its output: (mean, passed, reason, lot, inspection, entry).

    A plan with more lines than the gauge is left open for the lab to
    finish, and the batch stays not-yet-inspected until it does.
    """
    from apps.inventory.models import Lot
    from apps.quality.models import Disposition, Inspection, Reading

    from .orders import ProductionEntry
    from .station import check_supervisor
    from .station_floor import _number

    lines = list(plan.lines.select_related("characteristic"))
    line = next((row for row in lines if row.derived_from == gauge), None)
    if line is None:
        raise ValidationError(f"{plan} has no {gauge} to check against.")
    values = [_number(value, f"A {gauge} reading") for value in readings or []]
    if len(values) < line.sample_size:
        raise ValidationError(f"Check the {gauge} {line.sample_size} times; "
                              f"{len(values)} were taken.")
    mean = sum(values, ZERO) / len(values)
    passed = line.passes(mean)
    reason = " ".join((reason or "").split())
    if not passed:
        check_supervisor(station, supervisor, operator)
        if not reason:
            raise ValidationError(f"Say why {noun} off its {gauge} is taken.")
    lab_to_finish = len(lines) > 1
    lot = Lot.objects.create(item=order.item, code=code, manufactured_on=shift_date)
    inspection = Inspection.objects.create(
        lot=lot, plan=plan, inspected_on=shift_date, inspected_by=operator.party,
        disposition="" if passed or lab_to_finish else Disposition.CONCESSION,
        decided_by=None if passed else supervisor.party, decision_note=reason,
    )
    for number, value in enumerate(values, start=1):
        Reading.objects.create(inspection=inspection, plan_line=line, value=value,
                               sample_reference=f"{prefix}{number}", instrument=instrument)
    if not lab_to_finish:
        inspection.post()
    entry = ProductionEntry.objects.create(
        work_order=order, entry_date=shift_date, warehouse=station.warehouse,
        quantity_produced=net, uom=order.item.uom, lot=lot, work_centre=machine.work_centre,
        machine=machine, memo=memo[:255],
    )
    entry.post()
    return mean, passed, reason, lot, inspection, entry


def void_gauged(row, station, supervisor, operator, reason, noun):
    """Withdraw a gauged batch: its output reversed, its inspection voided or removed."""
    from .station import check_supervisor

    if row.station_id != station.pk:
        raise ValidationError(f"{row} was not weighed at {station}.")
    if row.voided_at is not None:
        raise ValidationError(f"{row} is already withdrawn.")
    if not (reason or "").strip():
        raise ValidationError(f"Say why the {noun} is withdrawn.")
    check_supervisor(station, supervisor, operator)
    row.entry.void(memo=f"Withdrawn at {station}: {reason.strip()}"[:255])
    open_with_the_lab = withdraw_inspection(row.inspection, reason.strip())
    if open_with_the_lab is not None:
        row.inspection = None
        discard_open(open_with_the_lab)
    row.voided_at = timezone.now()
    row.save(update_fields=["voided_at", "inspection", "updated_at"])
    return row


def withdraw_inspection(inspection, reason):
    """
    What withdrawing a weighed batch does to its inspection: voided if it
    stands, left as it is if quality voided it already, and handed back
    if it is still open with the lab, for the caller to discard once its
    row no longer names it.

    One answer for every weighed batch — a doff, a film roll, a bundle of
    bags — so that an inspection voided first cannot stop one of them and
    not the others: a bag count's did, and its 500 bags stayed on the
    shelf with nothing left to withdraw them by.
    """
    if inspection is None or not inspection.posted:
        return inspection
    if inspection.voided_at is None:
        inspection.void(reason)
    return None


def discard_open(inspection):
    """An inspection still open with the lab, for a batch no longer there."""
    inspection.readings.all().delete()
    inspection.delete()
