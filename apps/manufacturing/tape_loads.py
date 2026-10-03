"""
Tape put on a loom: which doffs a fabric roll was woven from.

A run's issues say which tape batches the run drew, not which loom got
which: twelve looms on one fabric share one run, and a roll with weak
weft could have come from any doff issued to it. So the loom exit
records the load itself — so many kilos of this doff on this loom's
warp creel or weft — and issues exactly that, by batch, to the run on
the loom. A doff's bobbins go to several looms, so it is never issued
whole.

**A roll's doffs are derived.** The loads on its loom for its run while
it was being woven — since the roll before came off — and, on each
side of the creel, the last load before that, which was still running.
Nothing is written on the roll, so withdrawing a load moves the answer.

**A load is withdrawn only while nothing was woven from it**: once a
roll has come off the loom since, that roll's trace names it.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel

ZERO = Decimal("0")


class CreelSide(models.TextChoices):
    WARP = "warp", "Warp creel"
    WEFT = "weft", "Weft"


class TapeLoad(AuditModel):
    station = models.ForeignKey("manufacturing.LoomStation", on_delete=models.PROTECT,
                                related_name="+")
    machine = models.ForeignKey("manufacturing.Machine", on_delete=models.PROTECT,
                                related_name="tape_loads")
    work_order = models.ForeignKey("manufacturing.WorkOrder", on_delete=models.PROTECT,
                                   related_name="tape_loads")
    side = models.CharField(max_length=4, choices=CreelSide.choices)
    lot = models.ForeignKey("inventory.Lot", on_delete=models.PROTECT, related_name="+")
    kg = models.DecimalField(max_digits=12, decimal_places=3)
    issue = models.OneToOneField("manufacturing.MaterialIssue", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="tape_load",
                                 editable=False)
    loaded_by = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    loaded_at = models.DateTimeField()
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["machine", "loaded_at", "id"]
        constraints = [
            models.CheckConstraint(check=Q(kg__gt=0), name="tape_load_kg_positive"),
        ]

    def __str__(self):
        return f"{self.lot.code} on {self.machine.code} {self.side}"


@transaction.atomic
def load_tape(station, operator, machine, code, kg, side, at=None):
    """Put `kg` of doff `code` on `machine`'s warp or weft, issued to its run by batch."""
    from apps.inventory.models import Lot

    from .machines import Machine
    from .orders import IssueDirection, MaterialIssue, MaterialIssueLine
    from .station import LineKind, run_on
    from .station_floor import _context, _number

    at = at or timezone.now()
    if station.kind != LineKind.LOOM_EXIT:
        raise ValidationError(f"{station} is not a loom station.")
    if side not in CreelSide.values:
        raise ValidationError(f"{side!r} is not warp or weft.")
    machine = Machine.objects.select_for_update().get(pk=machine.pk)
    if not station.machines.filter(pk=machine.pk).exists():
        raise ValidationError(f"{station} does not load {machine.code}.")
    _shift, shift_date = _context(station, operator, machine, at)
    run = run_on(machine)
    lot = Lot.objects.filter(code=(code or "").strip()).first()
    if lot is None:
        raise ValidationError(f"No doff {code}.")
    if not run.components.filter(item=lot.item).exists():
        raise ValidationError(f"{lot} is {lot.item}; {run} does not weave it.")
    kg = _number(kg, "The kilos loaded")
    on_hand = lot.on_hand_at(station.warehouse)
    if kg > on_hand:
        raise ValidationError(f"{lot} has {format(on_hand.normalize(), 'f')} kg in "
                              f"{station.warehouse}, not {format(kg.normalize(), 'f')}.")
    issue = None
    if not run.backflush:
        issue = MaterialIssue.objects.create(
            work_order=run, direction=IssueDirection.ISSUE, issue_date=shift_date,
            warehouse=station.warehouse, memo=f"Loaded on {machine.code} {side}")
        MaterialIssueLine.objects.create(issue=issue, item=lot.item, quantity=kg,
                                         uom=lot.item.uom, lot=lot, line_number=1)
        issue.post(memo=f"Loaded on {machine.code} {side} at {station}")
    return TapeLoad.objects.create(station=station, machine=machine, work_order=run,
                                   side=side, lot=lot, kg=kg, issue=issue,
                                   loaded_by=operator, loaded_at=at)


@transaction.atomic
def void_load(load, station, supervisor, operator, reason):
    from .rolls import FabricRoll
    from .station import check_supervisor

    if load.station_id != station.pk:
        raise ValidationError(f"{load} was not loaded at {station}.")
    if load.voided_at is not None:
        raise ValidationError(f"{load} is already withdrawn.")
    reason = " ".join((reason or "").split())
    if not reason:
        raise ValidationError("Say why the load is withdrawn.")
    check_supervisor(station, supervisor, operator)
    if FabricRoll.objects.filter(machine=load.machine, weighed_at__gte=load.loaded_at,
                                 entry__work_order=load.work_order,
                                 entry__voided_at__isnull=True).exists():
        raise ValidationError(f"A roll has come off {load.machine.code} since {load} was "
                              "loaded, and names it.")
    if load.issue_id:
        load.issue.void(memo=f"Load withdrawn at {station}: {reason}"[:255])
    load.voided_at = timezone.now()
    load.voided_reason = reason[:255]
    load.save(update_fields=["voided_at", "voided_reason", "updated_at"])
    return load


def tape_for(roll):
    """
    The loads a fabric roll was woven from, oldest first: those made on
    its loom for its run since the roll before it, and on each side the
    last one before that, still on the creel.
    """
    from .rolls import FabricRoll

    run = roll.entry.work_order
    before = FabricRoll.objects.filter(
        machine=roll.machine, entry__work_order=run, entry__voided_at__isnull=True,
        weighed_at__lt=roll.weighed_at).order_by("-weighed_at").first()
    loads = TapeLoad.objects.filter(machine=roll.machine, work_order=run,
                                    voided_at__isnull=True,
                                    loaded_at__lte=roll.weighed_at).select_related("lot")
    since = before.weighed_at if before is not None else None
    found = list(loads.filter(loaded_at__gt=since)) if since else list(loads)
    if since is not None:
        for side in CreelSide.values:
            running = loads.filter(side=side, loaded_at__lte=since).order_by(
                "-loaded_at", "-id").first()
            if running is not None:
                found.append(running)
    return sorted(found, key=lambda load: (load.loaded_at, load.pk))
