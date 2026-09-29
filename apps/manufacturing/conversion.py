"""
Bags counted off a cutting and stitching machine, and weighed before
they are booked.

The station is the same shop-floor station the looms use: a PIN for who
is working, a supervisor's PIN for what needs one, locked after wrong
guesses. It serves whatever machines it is given, so a fix to how
people sign in is one fix.

**Weight is the contract.** Each bundle counted is a batch, and a
sample of it is weighed against the bag specification's inspection
plan: its contracted weight, or its computed one where none was
contracted, give or take the tolerance, judged on the mean. The check
is a quality inspection like any other, so its limits are frozen on the
readings, a certificate reads it, and the release gate sees it.

**Off weight is not booked by the operator.** A bundle whose sample mean
is outside the limits goes into stock only if a station supervisor, not
the operator, takes it by concession and says why. Otherwise nothing is
recorded: no batch, no stock, no bags to anybody's name. Rolls off a
loom are booked with the exception flagged; bags are sold by weight, so
here the check comes first.

**Per machine and per operator.** Every count names both, so the plant
can see what each machine and each person turned out and how often it
was off weight, and payroll can pay bags by the piece. Bags on a
contractor's machine are the contractor's to pay. A count voided by a
supervisor voids its production and its inspection, and drops out of
both.
"""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel
from apps.core.windows import covers

ZERO = Decimal("0")
WEIGHT_CODE = "BAGWT"


class BagCount(AuditModel):
    station = models.ForeignKey("manufacturing.LoomStation", on_delete=models.PROTECT,
                                related_name="bag_counts")
    machine = models.ForeignKey("manufacturing.Machine", on_delete=models.PROTECT,
                                related_name="bag_counts")
    work_order = models.ForeignKey("manufacturing.WorkOrder", on_delete=models.PROTECT,
                                   related_name="bag_counts")
    operator = models.ForeignKey("hr.Employee", on_delete=models.PROTECT,
                                 related_name="bags_counted")
    supervisor = models.ForeignKey(
        "hr.Employee", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Who took an off-weight bundle by concession.")
    shift = models.ForeignKey("manufacturing.Shift", on_delete=models.PROTECT,
                              related_name="+")
    shift_date = models.DateField()
    counted_at = models.DateTimeField()
    bags = models.PositiveIntegerField()
    target_grams = models.DecimalField(
        max_digits=10, decimal_places=3, editable=False,
        help_text="The weight it was checked against, frozen with the count.")
    sample_mean_grams = models.DecimalField(max_digits=10, decimal_places=3, editable=False)
    passed = models.BooleanField(editable=False)
    entry = models.OneToOneField("manufacturing.ProductionEntry", on_delete=models.PROTECT,
                                 related_name="bag_count")
    inspection = models.OneToOneField("quality.Inspection", on_delete=models.PROTECT,
                                      related_name="bag_count")
    voided_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="+")
    void_reason = models.CharField(max_length=255, blank=True)
    mount = models.ForeignKey(
        "manufacturing.RollMount", null=True, blank=True, on_delete=models.PROTECT,
        related_name="bag_counts", editable=False,
        help_text="The roll on the machine when the bundle was counted.")

    class Meta:
        ordering = ["-counted_at", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(bags__gt=0), name="bag_count_positive"),
        ]

    def __str__(self):
        return f"{self.bags} bags on {self.machine.code} by {self.operator}"

    def is_standing(self):
        return self.entry.voided_at is None

    def save(self, *args, **kwargs):
        if not self._state.adding and not getattr(self, "_voiding", False):
            raise ValidationError("A count is what was counted. Void it and count again.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A count is what was counted; void it.")


def bag_specification_for(item, on_date):
    """The one bag specification in force for an item on a day."""
    from .woven import BagSpecification

    specs = [spec for spec in BagSpecification.objects.filter(bag_item=item, is_active=True)
             if covers(spec.valid_from, spec.valid_to, on_date)]
    if len(specs) != 1:
        raise ValidationError(
            f"{item} has {len(specs) or 'no'} bag specification"
            f"{'s' if len(specs) > 1 else ''} in force on {on_date}; the weight a bag "
            "should be comes from exactly one."
        )
    return specs[0]


def bag_code(machine, shift, shift_date):
    """BG-260601-D-C1-02: day, shift, machine, and the bundle's number on it."""
    prefix = f"BG-{shift_date:%y%m%d}-{shift.code}-{machine.code.replace('-', '')}-"
    taken = BagCount.objects.filter(machine=machine, shift=shift,
                                    shift_date=shift_date).count()
    return f"{prefix}{taken + 1:02d}"


def _check_supervisor(station, supervisor, operator, shift_date, what):
    if supervisor is None:
        raise ValidationError(f"{what} needs a supervisor's PIN.")
    if supervisor.pk == operator.pk:
        raise ValidationError(f"{what} is decided by somebody else, not the operator.")
    if not station.supervisors.filter(pk=supervisor.pk).exists():
        raise ValidationError(f"{supervisor} does not supervise {station}.")
    if not supervisor.is_working_on(shift_date):
        raise ValidationError(f"{supervisor} does not work here on {shift_date}.")


@transaction.atomic
def record_bags(station, operator, machine, bags, sample_grams, supervisor=None, reason="",
                at=None, instrument=None):
    """Count a bundle off `machine`, weigh its sample, and book it if it may be booked."""
    from apps.inventory.models import Lot
    from apps.quality.models import Disposition, Inspection, Reading

    from .machines import Machine
    from .orders import ProductionEntry
    from .shifts import Shift
    from .station import run_on

    at = at or timezone.now()
    if not station.is_active:
        raise ValidationError(f"{station} is not in use.")
    if operator is None:
        raise ValidationError("Nobody is signed in at this station.")
    machine = Machine.objects.select_for_update().get(pk=machine.pk)
    if not station.machines.filter(pk=machine.pk).exists():
        raise ValidationError(f"{station} does not count for {machine.code}.")
    try:
        bags = int(bags)
    except (TypeError, ValueError):
        raise ValidationError("Count the bags in whole bags.")
    if bags <= 0:
        raise ValidationError("Count at least one bag.")
    shift = Shift.covering(at)
    if shift is None:
        raise ValidationError(f"No shift runs at {timezone.localtime(at):%H:%M}.")
    shift_date = shift.shift_date_for(at)
    if not operator.is_working_on(shift_date):
        raise ValidationError(f"{operator} does not work here on {shift_date}.")

    order = run_on(machine)
    if order.item.tracking == "none":
        raise ValidationError(
            f"{order.item} is not tracked by batch. A bundle checked by weight is a batch; "
            "track the item by batch so the check has something to belong to."
        )
    spec = bag_specification_for(order.item, shift_date)
    plan = spec.inspection_plan
    if plan is None:
        raise ValidationError(f"{spec} has no inspection plan to weigh against.")
    lines = list(plan.lines.select_related("characteristic"))
    if [line.characteristic.code for line in lines] != [WEIGHT_CODE]:
        raise ValidationError(
            f"{plan} asks for more than the bag weight; inspect these bags in quality."
        )
    (line,) = lines
    try:
        values = [Decimal(str(value)) for value in sample_grams]
    except (ArithmeticError, TypeError, ValueError):
        raise ValidationError("Each sample weight is a number of grammes.")
    if len(values) < line.sample_size:
        raise ValidationError(
            f"Weigh {line.sample_size} bags from the bundle; {len(values)} were weighed."
        )
    if any(value <= 0 for value in values):
        raise ValidationError("A bag weighs something.")
    mean = sum(values, ZERO) / len(values)
    passed = line.passes(mean)
    reason = " ".join((reason or "").split())
    if not passed:
        _check_supervisor(station, supervisor, operator, shift_date,
                          f"A bundle averaging {mean.quantize(Decimal('0.001'))} g, outside "
                          f"{line.lower_limit}–{line.upper_limit} g,")
        if not reason:
            raise ValidationError("Say why the off-weight bundle is taken.")

    lot = Lot.objects.create(item=order.item, code=bag_code(machine, shift, shift_date),
                             manufactured_on=shift_date)
    inspection = Inspection.objects.create(
        lot=lot, plan=plan, inspected_on=shift_date, inspected_by=operator.party,
        disposition="" if passed else Disposition.CONCESSION,
        decided_by=None if passed else supervisor.party, decision_note=reason,
    )
    for number, value in enumerate(values, start=1):
        Reading.objects.create(inspection=inspection, plan_line=line, value=value,
                               sample_reference=f"S{number}", instrument=instrument)
    inspection.post()
    entry = ProductionEntry.objects.create(
        work_order=order, entry_date=shift_date, warehouse=station.warehouse,
        quantity_produced=Decimal(bags), uom=order.uom, lot=lot,
        work_centre=machine.work_centre, machine=machine, memo=f"Counted at {station}",
    )
    entry.post()
    from .process_rolls import current_mount

    mounted = current_mount(machine)
    return BagCount.objects.create(
        mount=mounted if mounted is not None and mounted.operation.work_order_id == order.pk
        else None,
        station=station, machine=machine, work_order=order, operator=operator,
        supervisor=None if passed else supervisor, shift=shift, shift_date=shift_date,
        counted_at=at, bags=bags, target_grams=(line.lower_limit + line.upper_limit) / 2,
        sample_mean_grams=mean.quantize(Decimal("0.001"), ROUND_HALF_UP), passed=passed,
        entry=entry, inspection=inspection,
    )


@transaction.atomic
def void_bags(count, supervisor, reason):
    """Take a count back: its production and its inspection with it."""
    if not count.is_standing():
        raise ValidationError(f"{count} is already void.")
    _check_supervisor(count.station, supervisor, count.operator, count.shift_date,
                      "Voiding a count")
    reason = " ".join((reason or "").split())
    if not reason:
        raise ValidationError("Say why the count is withdrawn.")
    count.entry.void(memo=f"Count voided: {reason}"[:255])
    count.inspection.void(reason)
    count.voided_by, count.void_reason = supervisor, reason
    count._voiding = True
    try:
        count.save(update_fields=["voided_by", "void_reason", "updated_at"])
    finally:
        # Left set, the next save of this object would pass the guard too.
        count._voiding = False
    return count


def bags_converted(employee, up_to):
    """For payroll: bags each day this person counted, standing, on the plant's own machines."""
    days = defaultdict(lambda: ZERO)
    for count in BagCount.objects.filter(
            operator=employee, shift_date__lte=up_to, entry__voided_at__isnull=True,
            machine__contractor__isnull=True):
        days[count.shift_date] += Decimal(count.bags)
    return sorted(days.items())


def summary(start, end, station=None):
    """Bags, bundles and off-weight bundles by machine and by operator, over a window."""
    counts = BagCount.objects.filter(shift_date__gte=start, shift_date__lte=end,
                                     entry__voided_at__isnull=True).select_related(
        "machine", "operator__party")
    if station is not None:
        counts = counts.filter(station=station)

    def fold(key_of, label_of):
        rows = {}
        for count in counts:
            key = key_of(count)
            row = rows.setdefault(key, {"who": label_of(count), "bundles": 0, "bags": 0,
                                        "conceded": 0, "deviation": ZERO})
            row["bundles"] += 1
            row["bags"] += count.bags
            row["conceded"] += 0 if count.passed else 1
            row["deviation"] += (count.sample_mean_grams - count.target_grams) / count.target_grams
        for row in rows.values():
            row["mean_deviation_percent"] = (row.pop("deviation") / row["bundles"] * 100).quantize(
                Decimal("0.01"), ROUND_HALF_UP)
        return sorted(rows.values(), key=lambda row: row["who"])

    return {
        "by_machine": fold(lambda c: c.machine_id, lambda c: c.machine.code),
        "by_operator": fold(lambda c: c.operator_id,
                            lambda c: f"{c.operator.employee_number} {c.operator.party.name}"),
    }
