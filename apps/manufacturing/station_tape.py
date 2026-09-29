"""
The tape line's station: a doff of bobbins weighed off the take-up.

The extrusion line winds tape on bobbins; a doff is a set of them taken
off together. At the station the operator puts the doff on the scale,
says how many bobbins it is (their tare comes off), and weighs a denier
check off the line — so many metres of one tape, weighed — for the
tape specification's inspection.

**The doff is a batch.** Numbered for the day, shift and line, booked
into stock against the run on the line, measured against the tape's
denier limits.

**The weight** is the scale's where the station's scale is bridged, and
a typed weight needs a supervisor's PIN and a reason, as at the loom
exit.

**Denier out of limits** is not booked without a supervisor's PIN and a
reason, the same rule a bundle of sacks off weight is held to.

**Strength is the lab's.** Where the specification also demands
tenacity or elongation, the line cannot measure them: the inspection is
left open with the denier readings on it for the lab to finish, and the
batch stays not-yet-inspected — which is what it is — until it does.

Withdrawn with a supervisor's PIN: the output reversed, its inspection
voided (or, still open, removed).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditModel

from .station_scale import TYPED_REASONS


class TapeDoff(AuditModel):
    station = models.ForeignKey("manufacturing.LoomStation", on_delete=models.PROTECT,
                                related_name="doffs")
    machine = models.ForeignKey("manufacturing.Machine", on_delete=models.PROTECT,
                                related_name="doffs")
    lot = models.OneToOneField("inventory.Lot", on_delete=models.PROTECT, related_name="doff")
    entry = models.ForeignKey("manufacturing.ProductionEntry", on_delete=models.PROTECT,
                              related_name="+")
    inspection = models.ForeignKey("quality.Inspection", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    gross_kg = models.DecimalField(max_digits=12, decimal_places=3)
    weight_source = models.CharField(
        max_length=8, default="scale",
        choices=[("scale", "Scale"), ("manual", "Manual, supervisor-approved")])
    scale_reading = models.OneToOneField(
        "manufacturing.ScaleReading", null=True, blank=True, on_delete=models.PROTECT,
        related_name="tape_doff", editable=False)
    weight_approved_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                           on_delete=models.PROTECT, related_name="+")
    typed_reason = models.CharField(max_length=16, blank=True, choices=TYPED_REASONS)
    typed_note = models.CharField(max_length=255, blank=True)
    bobbins = models.PositiveIntegerField()
    core_type = models.ForeignKey("manufacturing.CoreType", on_delete=models.PROTECT,
                                  related_name="+")
    net_kg = models.DecimalField(max_digits=12, decimal_places=3)
    mean_denier = models.DecimalField(max_digits=10, decimal_places=2)
    weighed_by = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    weighed_at = models.DateTimeField()
    shift = models.ForeignKey("manufacturing.Shift", on_delete=models.PROTECT, related_name="+")
    shift_date = models.DateField()
    conceded_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=255, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-weighed_at"]
        constraints = [
            models.CheckConstraint(check=models.Q(net_kg__gt=0), name="tape_doff_net_positive"),
            models.CheckConstraint(
                check=~models.Q(weight_source="manual")
                | (models.Q(weight_approved_by__isnull=False) & ~models.Q(typed_reason="")),
                name="tape_doff_typed_weight_is_approved"),
        ]

    def __str__(self):
        return self.lot.code


def doff_code(machine, shift, shift_date):
    """TP-261007-D-EXT1-03: day, shift, line, and the doff's number on it."""
    count = TapeDoff.objects.filter(machine=machine, shift=shift, shift_date=shift_date).count()
    line = machine.code.replace("-", "").upper()
    return f"TP-{shift_date:%y%m%d}-{shift.code[:1].upper()}-{line}-{count + 1:02d}"


@transaction.atomic
def record_tape(station, operator, machine, gross_kg, bobbins, core_type, denier_readings,
                supervisor=None, reason="", at=None, instrument=None, source="scale",
                typed_reason="", typed_note=""):
    from .machines import Machine
    from .station import LineKind, run_on
    from .station_floor import _context
    from .station_gauge import gauged_batch, typed_fields, weigh_gross
    from .woven import TapeSpecification

    at = at or timezone.now()
    if station.kind != LineKind.EXTRUSION:
        raise ValidationError(f"{station} is not a tape line's station.")
    machine = Machine.objects.select_for_update().get(pk=machine.pk)
    shift, shift_date = _context(station, operator, machine, at)
    gross, reading = weigh_gross(station, operator, supervisor, gross_kg, source, at,
                                 typed_reason, typed_note)
    try:
        bobbins = int(bobbins)
    except (TypeError, ValueError):
        raise ValidationError("Count the bobbins in whole bobbins.")
    if bobbins <= 0:
        raise ValidationError("A doff is at least one bobbin.")
    net = gross - core_type.tare_kg * bobbins
    if net <= 0:
        raise ValidationError(f"{gross} kg is not more than {bobbins} empty bobbins weigh.")
    order = run_on(machine)
    spec = TapeSpecification.objects.filter(bom=order.bom).first()
    if spec is None:
        raise ValidationError(f"{order} is not made to a tape specification, so there is "
                              "no denier to check against.")
    # A tape specification's plan always has its denier line: it is
    # generated from the specification, never typed.
    mean, passed, reason, lot, inspection, entry = gauged_batch(
        station, operator, machine, order, spec.inspection_plan, "denier", denier_readings,
        "tape", supervisor, reason, shift_date, doff_code(machine, shift, shift_date), net,
        f"Doffed at {station}", instrument=instrument)
    return TapeDoff.objects.create(
        station=station, machine=machine, lot=lot, entry=entry, inspection=inspection,
        gross_kg=gross, scale_reading=reading,
        **typed_fields(source, supervisor, typed_reason, typed_note),
        bobbins=bobbins, core_type=core_type, net_kg=net,
        mean_denier=mean.quantize(Decimal("0.01")), weighed_by=operator, weighed_at=at,
        shift=shift, shift_date=shift_date, conceded_by=None if passed else supervisor,
        reason=reason,
    )


@transaction.atomic
def void_doff(doff, station, supervisor, operator, reason):
    from .station_gauge import void_gauged

    return void_gauged(doff, station, supervisor, operator, reason, "doff")
