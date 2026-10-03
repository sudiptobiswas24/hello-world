"""
The blown-film line's station: a roll of liner film weighed off the winder.

The operator puts the roll on the scale, says its core and the metres on
the winder's counter, and gauges its thickness across the tube. The roll
is a batch, numbered for the day, shift and line, and booked into stock
against the film run on the line.

**Thickness** is judged on the gauge, against the film specification's
limits, and a roll outside them is taken only with a supervisor's PIN
and a reason. **The weight** gives a second thickness — what a tube of
that width and length would have to be to weigh that — recorded beside
the gauge's: a gauge reads a few points, the weight reads all of it, and
the two drifting apart is a gauge band the spot checks missed.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditModel

from .station_scale import TYPED_REASONS


class FilmRoll(AuditModel):
    station = models.ForeignKey("manufacturing.LoomStation", on_delete=models.PROTECT,
                                related_name="film_rolls")
    machine = models.ForeignKey("manufacturing.Machine", on_delete=models.PROTECT,
                                related_name="film_rolls")
    lot = models.OneToOneField("inventory.Lot", on_delete=models.PROTECT,
                               related_name="film_roll")
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
        related_name="film_roll", editable=False)
    weight_approved_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                           on_delete=models.PROTECT, related_name="+")
    typed_reason = models.CharField(max_length=16, blank=True, choices=TYPED_REASONS)
    typed_note = models.CharField(max_length=255, blank=True)
    core_type = models.ForeignKey("manufacturing.CoreType", on_delete=models.PROTECT,
                                  related_name="+")
    net_kg = models.DecimalField(max_digits=12, decimal_places=3)
    metres = models.DecimalField(max_digits=12, decimal_places=2)
    mean_micron = models.DecimalField(max_digits=8, decimal_places=2,
                                      help_text="What the gauge read, on average.")
    weighed_micron = models.DecimalField(
        max_digits=8, decimal_places=2,
        help_text="What the roll's weight over its length and width says.")
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
            models.CheckConstraint(check=models.Q(net_kg__gt=0) & models.Q(metres__gt=0),
                                   name="film_roll_is_a_real_roll"),
            models.CheckConstraint(
                check=~models.Q(weight_source="manual")
                | (models.Q(weight_approved_by__isnull=False) & ~models.Q(typed_reason="")),
                name="film_roll_typed_weight_is_approved"),
        ]

    def __str__(self):
        return self.lot.code


def film_code(machine, shift, shift_date):
    """LF-261007-D-BF1-03: day, shift, line, and the roll's number on it."""
    count = FilmRoll.objects.filter(machine=machine, shift=shift, shift_date=shift_date).count()
    line = machine.code.replace("-", "").upper()
    return f"LF-{shift_date:%y%m%d}-{shift.code[:1].upper()}-{line}-{count + 1:02d}"


@transaction.atomic
def record_film(station, operator, machine, gross_kg, core_type, metres, micron_readings,
                supervisor=None, reason="", at=None, instrument=None, source="scale",
                typed_reason="", typed_note=""):
    from .liners import FilmSpecification
    from .machines import Machine
    from .station import LineKind, run_on
    from .station_floor import _context, _number
    from .station_gauge import gauged_batch, typed_fields, weigh_gross
    from .woven import GRAMMES_PER_KG

    at = at or timezone.now()
    if station.kind != LineKind.BLOWN_FILM:
        raise ValidationError(f"{station} is not a blown-film line's station.")
    machine = Machine.objects.select_for_update().get(pk=machine.pk)
    shift, shift_date = _context(station, operator, machine, at)
    gross, reading = weigh_gross(station, operator, supervisor, gross_kg, source, at,
                                 typed_reason, typed_note)
    metres = _number(metres, "The metres")
    net = gross - core_type.tare_kg
    if net <= 0:
        raise ValidationError(f"{gross} kg is not more than the {core_type} core.")
    order = run_on(machine)
    spec = FilmSpecification.objects.filter(bom=order.bom).first()
    if spec is None:
        raise ValidationError(f"{order} is not made to a film specification, so there is "
                              "no thickness to check against.")
    # The film's weight a metre at one micron: the thickness the roll's
    # weight says it has is its weight a metre over that.
    per_micron = spec.grams_per_metre() / spec.micron
    weighed = net * GRAMMES_PER_KG / metres / per_micron
    mean, passed, reason, lot, inspection, entry = gauged_batch(
        station, operator, machine, order, spec.inspection_plan, "micron", micron_readings,
        "film", supervisor, reason, shift_date, film_code(machine, shift, shift_date), net,
        f"Wound off at {station}", instrument=instrument, prefix="M")
    return FilmRoll.objects.create(
        station=station, machine=machine, lot=lot, entry=entry, inspection=inspection,
        gross_kg=gross, scale_reading=reading,
        **typed_fields(source, supervisor, typed_reason, typed_note),
        core_type=core_type, net_kg=net, metres=metres,
        mean_micron=mean.quantize(Decimal("0.01")),
        weighed_micron=weighed.quantize(Decimal("0.01")), weighed_by=operator, weighed_at=at,
        shift=shift, shift_date=shift_date, conceded_by=None if passed else supervisor,
        reason=reason,
    )


@transaction.atomic
def void_film(roll, station, supervisor, operator, reason):
    from .process_rolls import RollMount
    from .station_gauge import void_gauged

    if RollMount.objects.filter(lot=roll.lot, voided_at__isnull=True).exists():
        raise ValidationError(f"{roll} has been mounted since; withdraw that first.")
    return void_gauged(roll, station, supervisor, operator, reason, "roll")
