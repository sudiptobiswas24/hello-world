"""
The loom exit station: where a roll comes off a loom, is weighed, and
becomes stock.

The pilot this was built from put one station at the end of a row of
contractor-run looms with a scale and a label printer. Three things
the paper register could not do, and this does:

**Every roll names a person.** The operator signs in with a PIN of
their own, issued rather than chosen, and every roll records who
weighed it. A shared login was the first thing the pilot ruled out: a
figure nobody owns is a figure nobody can be asked about.

**The scale checks the contractor.** An in-premises weaving contractor
is paid by the metre and declares the metres off the loom's counter.
The scale says what the roll weighs, and at the specification's
weight per metre that is how many metres it can hold. The two are
compared at a tolerance the plant sets. Over it, the roll is still
labelled — the fabric is real, and stopping the line punishes the
wrong people — but the variance is recorded against the contractor
and reaches the morning report.

**A typed weight needs a second person.** When the scale is down a
weight can be entered by hand, but only with a supervisor's PIN and a
reason, and the supervisor may not be the operator. Every override is
in the morning report.

A roll weighed here is an ordinary production entry and an ordinary
`FabricRoll` on an ordinary batch: costing, genealogy and stock see
nothing special about it.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, serialised
from apps.core.windows import covers

ONE_HUNDRED = Decimal("100")
LOCK_AFTER = 5
LOCK_WINDOW = datetime.timedelta(minutes=10)


class LineKind(models.TextChoices):
    LOOM_EXIT = "loom_exit", "Loom exit"
    CONVERSION = "conversion", "Cutting and stitching"
    EXTRUSION = "extrusion", "Tape line"
    PRINTING = "printing", "Printing"
    COATING = "coating", "Coating and lamination"
    BLOWN_FILM = "blown_film", "Blown film"


class LoomStation(AuditModel):
    """
    A terminal on the floor: at the end of a row of looms, beside the
    tape line's take-up, the printer, the coater, the cutting tables.

    Every station books stoppages, step counts, scrap and machine
    clocks for the machines it serves; what else it weighs depends on
    its kind (rolls at a loom exit, bundles at conversion, doffs of tape
    at a tape line, coating weight at a coater). A printer's station
    has nothing of its own to weigh: it counts printed sacks, and books
    stoppages, spoils and its clock like any other.
    """

    kind = models.CharField(max_length=16, choices=LineKind.choices,
                            default=LineKind.LOOM_EXIT)
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    warehouse = models.ForeignKey(
        "inventory.Warehouse", on_delete=models.PROTECT, related_name="+",
        help_text="Where rolls weighed here go into stock.",
    )
    machines = models.ManyToManyField(
        "manufacturing.Machine", related_name="stations",
        help_text="The looms this station weighs for.",
    )
    supervisors = models.ManyToManyField(
        "hr.Employee", blank=True, related_name="supervised_stations",
        help_text="Who may approve a typed weight here.",
    )
    scale_code = models.CharField(max_length=32, blank=True)
    scale_bridged = models.BooleanField(
        default=False,
        help_text="The scale posts its readings through a bridge: a scale weight "
                  "here is what the scale reported, never a typed figure.",
    )
    printer_code = models.CharField(max_length=32, blank=True)
    metres_tolerance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("2.00"),
        help_text="How far declared metres may sit from what the weight "
                  "supports before the roll is an exception.",
    )
    unaccounted_threshold_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("1.50"),
        help_text="Tape unaccounted for, as a share of tape consumed, above "
                  "which the morning report flags it.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        permissions = [("weigh_at_station", "Can run a loom exit station")]
        constraints = [
            models.CheckConstraint(
                check=Q(metres_tolerance_percent__gt=0)
                & Q(unaccounted_threshold_percent__gt=0),
                name="station_limits_positive",
            ),
            models.CheckConstraint(check=Q(scale_bridged=False) | ~Q(scale_code=""),
                                   name="bridged_station_names_its_scale"),
        ]

    def __str__(self):
        return self.code

    # -- who is at the station -----------------------------------------

    def is_locked(self, now=None):
        """
        Five wrong PINs in ten minutes lock the station for ten.

        A six-digit PIN is a million guesses; a station that lets
        anyone try them at keypad speed is not a login. Counted per
        station because that is where the guessing happens.
        """
        now = now or timezone.now()
        # Newest first, and by insertion where two share a clock
        # reading: otherwise an earlier success can sort ahead of the
        # failures after it and quietly reset the count.
        recent = list(
            self.attempts.filter(at__gte=now - LOCK_WINDOW).order_by("-at", "-id")
        )
        failures = []
        for attempt in recent:
            if attempt.succeeded:
                break
            failures.append(attempt)
        return len(failures) >= LOCK_AFTER

    def identify(self, pin, now=None):
        """The person a PIN belongs to, counting the attempt either way."""
        from apps.hr.models import Employee

        now = now or timezone.now()
        if self.is_locked(now):
            raise ValidationError(
                "Too many wrong PINs. This station is locked for ten minutes."
            )
        person = Employee.by_pin(pin, on_date=timezone.localtime(now).date())
        StationAttempt.objects.create(station=self, at=now, succeeded=person is not None)
        if person is None:
            raise ValidationError("That PIN is not recognised.")
        return person


class StationAttempt(models.Model):
    """One PIN typed at a station, right or wrong."""

    station = models.ForeignKey(LoomStation, on_delete=models.CASCADE, related_name="attempts")
    at = models.DateTimeField()
    succeeded = models.BooleanField()

    class Meta:
        ordering = ["-at"]


class CoreType(AuditModel):
    """The tube a roll is wound on, and what it weighs: the tare master."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128, blank=True)
    tare_kg = models.DecimalField(max_digits=8, decimal_places=3)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(check=Q(tare_kg__gte=0), name="core_tare_not_negative"),
        ]

    def __str__(self):
        return f"{self.code} ({self.tare_kg} kg)"


class VoidedMeasurement(models.Model):
    """
    A figure taken at the station that turned out wrong. Not edited and
    not deleted: voided, with a supervisor's name, and replaced.
    """

    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_by = models.ForeignKey("hr.Employee", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+", editable=False)

    class Meta:
        abstract = True

    @serialised("voided_at")
    def void(self, supervisor, operator, station, at=None):
        if self.voided_at is not None:
            raise ValidationError("This figure is already withdrawn.")
        check_supervisor(station, supervisor, operator)
        self.voided_at = at or timezone.now()
        self.voided_by = supervisor
        self.save(update_fields=["voided_at", "voided_by", "updated_at"])


def check_supervisor(station, supervisor, operator):
    """Somebody else, who approves at this station: the rule for a typed weight."""
    if supervisor is None:
        raise ValidationError("A correction needs a supervisor's PIN.")
    if operator is not None and supervisor.pk == operator.pk:
        raise ValidationError("A correction is approved by somebody else.")
    if not station.supervisors.filter(pk=supervisor.pk).exists():
        raise ValidationError(f"{supervisor} does not approve at {station}.")


class TapeCount(VoidedMeasurement, AuditModel):
    """
    Tape found on a contractor's looms when the shift-day closed.

    Recorded against the shift-day it closes, so the next day's opening
    figure is simply this one. A day with either count missing cannot
    be balanced, and the morning report says so rather than counting
    the missing tape as none. A count typed wrong is voided with a
    supervisor's PIN and counted again.
    """

    station = models.ForeignKey(LoomStation, on_delete=models.PROTECT, related_name="tape_counts")
    contractor = models.ForeignKey("core.Party", on_delete=models.PROTECT, related_name="+")
    shift_date = models.DateField()
    kg = models.DecimalField(max_digits=12, decimal_places=3)
    counted_by = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    counted_at = models.DateTimeField()

    class Meta:
        ordering = ["-shift_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["station", "contractor", "shift_date"],
                condition=Q(voided_at__isnull=True),
                name="one_tape_count_per_contractor_per_day",
            ),
            models.CheckConstraint(check=Q(kg__gte=0), name="tape_count_not_negative"),
        ]


class LoomWaste(VoidedMeasurement, AuditModel):
    """
    Loom waste weighed at the station: the tape that became neither
    fabric nor anything else. A measurement for the tape balance, like
    the paper register it replaces; it is not a movement of stock.
    """

    station = models.ForeignKey(LoomStation, on_delete=models.PROTECT, related_name="waste")
    contractor = models.ForeignKey("core.Party", on_delete=models.PROTECT, related_name="+")
    shift_date = models.DateField()
    kg = models.DecimalField(max_digits=12, decimal_places=3)
    weighed_by = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    weighed_at = models.DateTimeField()

    class Meta:
        ordering = ["-weighed_at"]
        constraints = [
            models.CheckConstraint(check=Q(kg__gt=0), name="loom_waste_positive"),
        ]


# -- what a loom is making ---------------------------------------------


def run_on(machine):
    """
    The released run this loom is weaving.

    A run whose operation names this loom wins over one that names only
    its bank; two of either is refused rather than guessed, because a
    roll booked to the wrong run is costed and traced to it for ever.
    """
    from .orders import WorkOrder, WorkOrderStatus

    runs = WorkOrder.objects.filter(
        status=WorkOrderStatus.RELEASED,
        operations__work_centre=machine.work_centre,
    ).filter(
        Q(operations__machine=machine) | Q(operations__machine__isnull=True)
    ).distinct()
    named = [run for run in runs if run.operations.filter(machine=machine).exists()]
    candidates = named or list(runs)
    if not candidates:
        raise ValidationError(f"No released run is on {machine.code}.")
    if len(candidates) > 1:
        raise ValidationError(
            f"{machine.code} is on {len(candidates)} released runs "
            f"({', '.join(run.number for run in candidates)}). Assign the "
            "loom on one of them so a roll knows which it belongs to."
        )
    from .machines import refusal

    # A run left to the bank may still be one this machine cannot make:
    # a lined sack on a BCS that inserts none is booked nowhere.
    reason = refusal(machine, candidates[0].bom)
    if reason:
        raise ValidationError(f"{candidates[0]} cannot be made here: {reason}.")
    return candidates[0]


def specification_for(item, on_date):
    """The fabric specification in force for an item on a date."""
    from .woven import FabricSpecification

    for spec in FabricSpecification.objects.filter(fabric_item=item):
        if covers(spec.valid_from, spec.valid_to, on_date):
            return spec
    return None


def roll_code(machine, shift, shift_date):
    """FR-261007-D-L17-07: day, shift, loom, and the roll's number on it."""
    from .rolls import FabricRoll

    count = FabricRoll.objects.filter(
        machine=machine, shift=shift, shift_date=shift_date
    ).count()
    loom = machine.code.replace("-", "").upper()
    return f"FR-{shift_date:%y%m%d}-{shift.code[:1].upper()}-{loom}-{count + 1:02d}"


# -- weighing a roll ----------------------------------------------------


@transaction.atomic
def record_roll(station, operator, machine, gross_kg, core_type, declared_m,
                source="scale", supervisor=None, reason="", note="", at=None, weaver=None):
    """
    Weigh a roll off `machine` and put it into stock.

    Returns the `FabricRoll`. A declared length outside the station's
    tolerance is recorded as an exception, not refused.

    `weaver` is who wove it, where the plant pays weavers by the piece.
    Not on a contractor's loom: the contractor pays its own weavers, and
    crediting one of ours with that cloth pays for it twice.
    """
    from apps.inventory.models import Lot

    from .machines import Machine
    from .orders import ProductionEntry
    from .rolls import FabricRoll
    from .shifts import Shift
    from .station_scale import check_typed_weight, gross_weight
    from .woven import Weave

    at = at or timezone.now()
    if not station.is_active:
        raise ValidationError(f"{station} is not in use.")
    if operator is None:
        raise ValidationError("Nobody is signed in at this station.")
    # One roll at a time per loom, so two confirmations cannot take the
    # same roll number.
    machine = Machine.objects.select_for_update().get(pk=machine.pk)
    if not station.machines.filter(pk=machine.pk).exists():
        raise ValidationError(f"{station} does not weigh for {machine.code}.")
    if weaver is not None and machine.contractor_id is not None:
        raise ValidationError(
            f"{machine.code} is run by {machine.contractor}, who pays its own weavers; "
            "no weaver of ours is credited with its cloth."
        )

    declared_m = Decimal(declared_m)
    if declared_m <= 0:
        raise ValidationError("Enter the metres the loom counter shows.")
    gross_kg, reading = gross_weight(station, gross_kg, source, at)
    net_kg = gross_kg - core_type.tare_kg
    if net_kg <= 0:
        raise ValidationError(
            f"{gross_kg} kg gross is not more than the {core_type} core. "
            "Check the core type, or weigh the roll again."
        )

    if source == "manual":
        check_typed_weight(station, operator, supervisor, reason, note)
    elif source != "scale":
        raise ValidationError(f"{source!r} is not a weight source.")

    shift = Shift.covering(at)
    if shift is None:
        raise ValidationError(f"No shift runs at {timezone.localtime(at):%H:%M}.")
    shift_date = shift.shift_date_for(at)
    for person, role in ((operator, "weighing"), (supervisor, "approving"),
                         (weaver, "weaving")):
        if person is not None and not person.is_working_on(shift_date):
            raise ValidationError(f"{person} does not work here on {shift_date}; not {role}.")

    order = run_on(machine)
    spec = specification_for(order.item, shift_date)
    if spec is None:
        raise ValidationError(
            f"No fabric specification for {order.item} is in force on "
            f"{shift_date}, so there is no weight per metre to check against."
        )

    lot = Lot.objects.create(
        item=order.item, code=roll_code(machine, shift, shift_date),
        manufactured_on=shift_date,
    )
    entry = ProductionEntry.objects.create(
        work_order=order, entry_date=shift_date, warehouse=station.warehouse,
        quantity_produced=net_kg, uom=order.item.uom, lot=lot,
        work_centre=machine.work_centre, machine=machine,
        memo=f"Weighed at {station}",
    )
    entry.post()

    roll = FabricRoll(
        lot=lot, specification=spec, entry=entry, machine=machine,
        width_mm=spec.lay_flat_width_cm * 10, length_m=declared_m,
        net_weight_kg=net_kg, core_weight_kg=core_type.tare_kg,
        is_tubular=spec.weave == Weave.TUBULAR,
        station=station, weighed_by=operator, weighed_at=at, woven_by=weaver,
        shift=shift, shift_date=shift_date, core_type=core_type,
        weight_source=source, scale_reading=reading,
        approved_by=supervisor if source == "manual" else None,
        override_reason=reason if source == "manual" else "",
        override_note=note.strip() if source == "manual" else "",
    )
    target = roll.target_gsm()
    derived = (
        net_kg * 1000 / (target * roll.layers() * roll.width_m())
    ).quantize(Decimal("0.01"))
    variance = ((declared_m - derived) / derived * ONE_HUNDRED).quantize(Decimal("0.01"))
    roll.metres_from_weight = derived
    roll.metres_variance_percent = variance
    roll.metres_tolerance_percent = station.metres_tolerance_percent
    roll.is_metres_exception = abs(variance) > station.metres_tolerance_percent
    roll.save()
    return roll


def latest_tape_lot(order):
    """The batch of tape most recently issued to a run, for the station to show."""
    from .orders import MaterialIssueLine

    line = MaterialIssueLine.objects.filter(
        issue__work_order=order, issue__posted=True, issue__voided_at__isnull=True,
        lot__isnull=False,
    ).select_related("lot", "issue").order_by("-issue__issue_date", "-id").first()
    return line.lot if line is not None else None
