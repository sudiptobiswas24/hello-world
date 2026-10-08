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

from apps.core.models import AuditModel, lock_rows

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
    # A load on a backflushed run stays on the shelf until the output draws
    # it, so what other creels already hold of the doff is not free: one
    # 100 kg doff loaded twice at 100 kg was taken both times.
    free = lot.on_hand_at(station.warehouse) - on_creels_undrawn(lot)
    if kg > free:
        raise ValidationError(f"{lot} has {format(free.normalize(), 'f')} kg in "
                              f"{station.warehouse} that no creel holds yet, not "
                              f"{format(kg.normalize(), 'f')}.")
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
    # Held before anything below reads what the run has made or drawn: a
    # roll booked meanwhile draws on this load.
    run = load.work_order
    lock_rows(run)
    if not run.is_open():
        raise ValidationError(
            f"{run} is {run.get_status_display().lower()}; {load} stands as the record of "
            "what it wove, and what its output did not draw is free to load on another run."
        )
    if FabricRoll.objects.filter(machine=load.machine, weighed_at__gte=load.loaded_at,
                                 entry__work_order=load.work_order,
                                 entry__voided_at__isnull=True).exists():
        raise ValidationError(f"A roll has come off {load.machine.code} since {load} was "
                              "loaded, and names it.")
    if run.backflush and drawn_by_output(run, load.lot) > loaded(run, load.lot) - load.kg:
        raise ValidationError(f"{run}'s output has drawn on {load}; it stands.")
    if load.issue_id:
        load.issue.void_with(load, memo=f"Load withdrawn at {station}: {reason}"[:255])
    load.voided_at = timezone.now()
    load.voided_reason = reason[:255]
    load.save(update_fields=["voided_at", "voided_reason", "updated_at"])
    return load


def loaded(run, lot):
    """Kilos of a batch on this run's creels, by its standing loads."""
    return sum((load.kg for load in TapeLoad.objects.filter(
        work_order=run, lot=lot, voided_at__isnull=True)), ZERO)


def drawn_by_output(run, lot):
    """What a backflushed run's own output has drawn of a batch, standing."""
    from .orders import MaterialIssueLine

    return sum((line.stock_quantity() for line in MaterialIssueLine.objects.filter(
        issue__work_order=run, lot=lot, issue__posted=True, issue__voided_at__isnull=True,
        issue__backflushed_by__isnull=False,
    ).select_related("item", "uom")), ZERO)


def on_the_creels(run, item):
    """
    [(batch, kg)] of `item` loaded on a backflushed run's creels and not
    yet drawn by its output, in the order it was loaded: what its output
    draws, by batch, since a batch-kept tape cannot be drawn as no batch.
    """
    batches = []
    for load in TapeLoad.objects.filter(work_order=run, lot__item=item, voided_at__isnull=True
                                        ).select_related("lot").order_by("loaded_at", "id"):
        if load.lot not in batches:
            batches.append(load.lot)
    rows = [(lot, loaded(run, lot) - drawn_by_output(run, lot)) for lot in batches]
    return [(lot, kg) for lot, kg in rows if kg > 0]


def on_creels_undrawn(lot):
    """
    Kilos of a batch on the creels of backflushed runs still open that
    their output has not drawn yet.

    A run closed or cancelled holds nothing on its creels. A backflushed
    load issues nothing, so what its output did not draw never left the
    shelf, and it is free to load on another run; the closed run's loads
    stand as the record of what it wove. Counted for ever, 20 kg left of
    a 100 kg doff after a run drew 80 and closed could never be loaded
    again, nor its load withdrawn.
    """
    from .orders import WorkOrder, WorkOrderStatus

    runs = WorkOrder.objects.filter(
        backflush=True, status=WorkOrderStatus.RELEASED,
        tape_loads__lot=lot, tape_loads__voided_at__isnull=True,
    ).distinct()
    return sum((max(loaded(run, lot) - drawn_by_output(run, lot), ZERO) for run in runs), ZERO)


def tape_for(roll):
    """
    The loads a fabric roll was woven from, oldest first: those made on
    its loom for its run since the roll before it, and on each side the
    last one before that, still on the creel.
    """
    from .rolls import FabricRoll

    if roll.weighed_at is None or roll.machine_id is None:
        # Booked in the office, not weighed off a loom: there is no moment
        # on a machine to read the creel at.
        raise ValidationError(f"{roll} was not weighed at a loom, so nothing records which "
                              "doffs were on the creel when it was woven.")
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
