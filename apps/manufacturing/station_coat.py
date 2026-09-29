"""
The coater's check: how much coating went on.

A laminated sack's coating is weighed, not seen. At the coater the
operator punches a disc out of the coated fabric and one out of the
same fabric uncoated, weighs both, and the difference over the disc's
area is the coating's GSM. Too heavy is polymer given away on every
metre; too light is a sack that sifts, or on a valve sack one that will
not weld.

**Checked against the sack's specification**: its coating GSM, within
its coating tolerance. The limits are recorded on the check as they
were, so a later change to the specification does not rewrite what
this roll was held to.

**Off the limits** is not booked without a supervisor's PIN and a
reason, the same rule the tape line and the bag count are held to.

**The metres are the count.** The coater's counter reads metres; a
metre of tube is a metre towards sacks cut at the specification's cut
length, so the metres coated count as that many whole sacks passed by
the coating step. The step after cannot take more than that.

Withdrawn with a supervisor's PIN: the count is voided with it (which
the count itself refuses once the step after has taken the sacks).
"""

from decimal import ROUND_FLOOR, Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditModel

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
SQ_CM_PER_SQ_M = Decimal("10000")
COATING_SAMPLES = 3
DISC_SQ_CM = Decimal("100")


class CoatingCheck(AuditModel):
    station = models.ForeignKey("manufacturing.LoomStation", on_delete=models.PROTECT,
                                related_name="coating_checks")
    machine = models.ForeignKey("manufacturing.Machine", on_delete=models.PROTECT,
                                related_name="coating_checks")
    specification = models.ForeignKey("manufacturing.BagSpecification",
                                      on_delete=models.PROTECT, related_name="+")
    report = models.OneToOneField("manufacturing.OperationReport", on_delete=models.PROTECT,
                                  related_name="coating_check")
    metres = models.DecimalField(max_digits=12, decimal_places=2)
    disc_sq_cm = models.DecimalField(max_digits=8, decimal_places=2)
    samples = models.JSONField(help_text="[[coated g, uncoated g], ...] as weighed.")
    target_gsm = models.DecimalField(max_digits=6, decimal_places=2)
    lower_gsm = models.DecimalField(max_digits=8, decimal_places=3)
    upper_gsm = models.DecimalField(max_digits=8, decimal_places=3)
    mean_gsm = models.DecimalField(max_digits=8, decimal_places=2)
    passed = models.BooleanField()
    checked_by = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    checked_at = models.DateTimeField()
    shift = models.ForeignKey("manufacturing.Shift", on_delete=models.PROTECT, related_name="+")
    shift_date = models.DateField()
    conceded_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=255, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-checked_at"]
        constraints = [
            models.CheckConstraint(check=models.Q(metres__gt=0),
                                   name="coating_check_metres_positive"),
        ]

    def __str__(self):
        return f"{self.machine.code} {self.metres} m at {self.mean_gsm} GSM"


def coating_limits(spec):
    """(target, lower, upper) GSM a laminated sack's coating is held to."""
    margin = spec.lamination_gsm * spec.lamination_tolerance_percent / ONE_HUNDRED
    return spec.lamination_gsm, spec.lamination_gsm - margin, spec.lamination_gsm + margin


def _grammes(pair):
    from .station_floor import _number

    try:
        coated, uncoated = pair
    except (TypeError, ValueError):
        raise ValidationError("Each sample is two weights: coated and uncoated.")
    return (_number(coated, "A coated disc's weight"),
            _number(uncoated, "An uncoated disc's weight"))


@transaction.atomic
def record_coating(station, operator, machine, metres, samples, supervisor=None, reason="",
                   at=None, disc_sq_cm=DISC_SQ_CM):
    from .conversion import bag_specification_for
    from .machines import Machine
    from .scrap import report
    from .station import LineKind, check_supervisor
    from .station_floor import _context, _number, _step

    at = at or timezone.now()
    if station.kind != LineKind.COATING:
        raise ValidationError(f"{station} is not a coater's station.")
    machine = Machine.objects.select_for_update().get(pk=machine.pk)
    shift, shift_date = _context(station, operator, machine, at)
    metres = _number(metres, "The metres coated")
    disc = _number(disc_sq_cm, "The disc's area")
    run, step = _step(machine)
    spec = bag_specification_for(run.item, shift_date)
    if not spec.is_laminated:
        raise ValidationError(f"{spec} is not laminated; there is no coating to check.")
    pairs = [_grammes(pair) for pair in samples or []]
    if len(pairs) < COATING_SAMPLES:
        raise ValidationError(f"Weigh {COATING_SAMPLES} pairs of discs; "
                              f"{len(pairs)} were weighed.")
    values = [(coated - uncoated) * SQ_CM_PER_SQ_M / disc for coated, uncoated in pairs]
    mean = sum(values, ZERO) / len(values)
    _target, lower, upper = coating_limits(spec)
    passed = lower <= mean <= upper
    reason = " ".join((reason or "").split())
    if not passed:
        check_supervisor(station, supervisor, operator)
        if not reason:
            raise ValidationError("Say why coating off its weight is taken.")
    sacks = (metres * ONE_HUNDRED / spec.cut_length_cm()).quantize(Decimal("1"), ROUND_FLOOR)
    if sacks < 1:
        raise ValidationError(f"{metres} m is less than one sack's cut length.")
    counted = report(step, sacks, on_date=shift_date, machine=machine,
                     memo=f"Coated {metres} m at {station}"[:255])
    check = CoatingCheck.objects.create(
        station=station, machine=machine, specification=spec, report=counted,
        metres=metres, disc_sq_cm=disc,
        samples=[[str(coated), str(uncoated)] for coated, uncoated in pairs],
        target_gsm=spec.lamination_gsm, lower_gsm=lower, upper_gsm=upper,
        mean_gsm=mean.quantize(Decimal("0.01")), passed=passed, checked_by=operator,
        checked_at=at, shift=shift, shift_date=shift_date,
        conceded_by=None if passed else supervisor, reason=reason,
    )
    # As stored: the places each column keeps, not the arithmetic's.
    return CoatingCheck.objects.select_related("report").get(pk=check.pk)


@transaction.atomic
def void_coating(check, station, supervisor, operator, reason):
    from .station import check_supervisor

    if check.station_id != station.pk:
        raise ValidationError(f"{check} was not checked at {station}.")
    if check.voided_at is not None:
        raise ValidationError(f"{check} is already withdrawn.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Say why the coating check is withdrawn.")
    check_supervisor(station, supervisor, operator)
    check.report.void(f"Withdrawn at {station}: {reason}"[:255])
    check.voided_at = timezone.now()
    check.save(update_fields=["voided_at", "updated_at"])
