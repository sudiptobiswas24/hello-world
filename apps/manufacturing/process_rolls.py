"""
Rolls through lamination, film lamination, flexo and the BCS, inside
the sack run.

Laminated and printed rolls are not stocked here: a fabric roll is
laminated, some go on to the flexo, and all go to a BCS, within one
run. What was missing was which roll was where. So each machine says
what is mounted on it, and each roll it makes is weighed, numbered and
tied to the roll it was made from:

**Mounted.** An operator scans the roll going onto a laminator, flexo or
BCS. A fabric roll from the loom shed is stock, so mounting it issues it
to the run by batch — the run now knows which rolls it drew, not just
how many kilos. A roll the plant made earlier in the same run (laminated,
printed) is mounted as itself. Mounting the next roll finishes the one
before; a roll taken off part-used goes back, and what is left returns
to the store at what it went out at.

**Weighed off.** At a laminator the roll off is weighed. Its gain over
the fabric that went into it — the fabric's own weight a metre, times
the metres that came off — is what the lamination added. Held against
what the sack's specification adds: its coating, and on a BOPP or
metallic sack the film as well, each over the fabric's area. Off the
limits is taken only with a supervisor's PIN and a reason. A printed roll
off the flexo is weighed and numbered too; ink is too light to check by
weight, so it records what it came from.

**Bundles.** A bundle counted at a BCS names the roll on the machine,
which names the roll it came from, back to the fabric roll and the loom.

Withdrawn with a supervisor's PIN, and only while nothing was made from
it: a mount gives its issue back to the store; a roll is taken off the
record.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel

from .station_scale import TYPED_REASONS

ZERO = Decimal("0")
GRAMMES_PER_KG = Decimal("1000")
CM_PER_M = Decimal("100")


class RollKind(models.TextChoices):
    COATED = "coated", "Laminated (coated)"
    BOPP = "bopp", "BOPP laminated"
    METALLIC = "metallic", "Metallic laminated"
    PRINTED = "printed", "Printed"


PREFIX = {RollKind.COATED: "LM", RollKind.BOPP: "BP", RollKind.METALLIC: "MT",
          RollKind.PRINTED: "PR"}


class RollMount(AuditModel):
    station = models.ForeignKey("manufacturing.LoomStation", on_delete=models.PROTECT,
                                related_name="mounts")
    machine = models.ForeignKey("manufacturing.Machine", on_delete=models.PROTECT,
                                related_name="mounts")
    operation = models.ForeignKey("manufacturing.WorkOrderOperation", on_delete=models.PROTECT,
                                  related_name="mounts")
    lot = models.ForeignKey("inventory.Lot", null=True, blank=True, on_delete=models.PROTECT,
                            related_name="mounts", help_text="A roll from stock.")
    roll = models.ForeignKey("manufacturing.ProcessRoll", null=True, blank=True,
                             on_delete=models.PROTECT, related_name="mounts",
                             help_text="A roll made earlier in this run.")
    issue = models.OneToOneField("manufacturing.MaterialIssue", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+", editable=False)
    returned = models.OneToOneField("manufacturing.MaterialIssue", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+", editable=False)
    mounted_by = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    mounted_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-mounted_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(Q(lot__isnull=False) & Q(roll__isnull=True))
                | (Q(lot__isnull=True) & Q(roll__isnull=False)),
                name="mount_is_a_stock_roll_or_one_made_here"),
            models.UniqueConstraint(
                fields=["machine"],
                condition=Q(finished_at__isnull=True, voided_at__isnull=True),
                name="one_roll_mounted_per_machine"),
        ]

    def __str__(self):
        return f"{self.roll_code()} on {self.machine.code}"

    def roll_code(self):
        return self.lot.code if self.lot_id else self.roll.code

    def is_standing(self):
        return self.voided_at is None

    def input_kg_per_metre(self):
        """What a metre of the mounted roll weighed."""
        from .rolls import FabricRoll

        if self.roll_id:
            return self.roll.net_kg / self.roll.metres
        fabric = FabricRoll.objects.filter(lot=self.lot).first()
        if fabric is None:
            return None
        return fabric.net_weight_kg / fabric.length_m


class ProcessRoll(AuditModel):
    code = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=12, choices=RollKind.choices)
    mount = models.ForeignKey(RollMount, on_delete=models.PROTECT, related_name="made",
                              help_text="What was on the machine when this came off it.")
    station = models.ForeignKey("manufacturing.LoomStation", on_delete=models.PROTECT,
                                related_name="+")
    machine = models.ForeignKey("manufacturing.Machine", on_delete=models.PROTECT,
                                related_name="+")
    core_type = models.ForeignKey("manufacturing.CoreType", on_delete=models.PROTECT,
                                  related_name="+")
    gross_kg = models.DecimalField(max_digits=12, decimal_places=3)
    net_kg = models.DecimalField(max_digits=12, decimal_places=3)
    metres = models.DecimalField(max_digits=12, decimal_places=2)
    weight_source = models.CharField(
        max_length=8, default="scale",
        choices=[("scale", "Scale"), ("manual", "Manual, supervisor-approved")])
    scale_reading = models.OneToOneField(
        "manufacturing.ScaleReading", null=True, blank=True, on_delete=models.PROTECT,
        related_name="process_roll", editable=False)
    weight_approved_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                           on_delete=models.PROTECT, related_name="+")
    typed_reason = models.CharField(max_length=16, blank=True, choices=TYPED_REASONS)
    typed_note = models.CharField(max_length=255, blank=True)
    registration_mm = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Off the press: how far out of register the colours are, the worst seen.")
    delta_e = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True,
                                  help_text="Off the press: shade against the approved proof.")
    design = models.ForeignKey("manufacturing.PrintDesign", null=True, blank=True,
                               on_delete=models.PROTECT, related_name="+",
                               help_text="The artwork confirmed on the press.")
    added_gsm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True,
                                    help_text="What the lamination added, measured.")
    expected_gsm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    lower_gsm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    upper_gsm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    passed = models.BooleanField(null=True)
    conceded_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=255, blank=True)
    weighed_by = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    weighed_at = models.DateTimeField()
    shift = models.ForeignKey("manufacturing.Shift", on_delete=models.PROTECT, related_name="+")
    shift_date = models.DateField()
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-weighed_at", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(net_kg__gt=0) & Q(metres__gt=0),
                                   name="process_roll_is_a_real_roll"),
            models.CheckConstraint(
                check=~Q(weight_source="manual")
                | (Q(weight_approved_by__isnull=False) & ~Q(typed_reason="")),
                name="process_roll_typed_weight_is_approved"),
        ]

    def __str__(self):
        return self.code

    def run(self):
        return self.mount.operation.work_order


def _open_mount(machine):
    return RollMount.objects.select_for_update().filter(
        machine=machine, finished_at__isnull=True, voided_at__isnull=True).first()


def _check_kind(station, kinds, what):
    from .station import LineKind

    if station.kind not in kinds:
        allowed = ", ".join(LineKind(kind).label.lower() for kind in kinds)
        raise ValidationError(f"{station} is not a station that {what} ({allowed}).")


def _issue_roll(run, lot, warehouse, on_date, label):
    from .orders import IssueDirection, MaterialIssue, MaterialIssueLine

    on_hand = lot.on_hand_at(warehouse)
    if on_hand <= 0:
        raise ValidationError(f"{lot} is not in {warehouse}: it has gone into a run already, "
                              "or it was never put there.")
    issue = MaterialIssue.objects.create(work_order=run, direction=IssueDirection.ISSUE,
                                         issue_date=on_date, warehouse=warehouse,
                                         memo=label[:255])
    MaterialIssueLine.objects.create(issue=issue, item=lot.item, quantity=on_hand,
                                     uom=lot.item.uom, lot=lot, line_number=1)
    issue.post(memo=label)
    return issue


@transaction.atomic
def mount_roll(station, operator, machine, code, at=None):
    """Put a roll on a laminator, flexo or BCS; the roll before is finished."""
    from apps.inventory.models import Lot
    from apps.quality.release import check_released

    from .machines import Machine
    from .station import LineKind
    from .station_floor import _context, _step

    at = at or timezone.now()
    _check_kind(station, (LineKind.COATING, LineKind.PRINTING, LineKind.CONVERSION),
                "mounts rolls")
    machine = Machine.objects.select_for_update().get(pk=machine.pk)
    _shift, shift_date = _context(station, operator, machine, at)
    run, step = _step(machine)
    code = (code or "").strip()
    made = ProcessRoll.objects.filter(code=code).first()
    if made is not None:
        if made.voided_at is not None:
            raise ValidationError(f"{made} was withdrawn.")
        if made.run().pk != run.pk:
            raise ValidationError(f"{made} was made for {made.run()}, not {run}.")
        if made.mounts.filter(voided_at__isnull=True).exists():
            raise ValidationError(f"{made} has already been mounted.")
        lot = None
    else:
        lot = Lot.objects.filter(code=code).first()
        if lot is None:
            raise ValidationError(f"No roll {code}.")
        if not run.components.filter(item=lot.item).exists():
            raise ValidationError(f"{lot} is {lot.item}; {run} does not use it.")
        # On every run, as a tape load asks: a backflushed run issues
        # nothing at the mount, so nothing else would.
        check_released(lot.item, lot, action="go into a run", warehouse=station.warehouse,
                       reworking=run.reworks(lot.item))
    previous = _open_mount(machine)
    if previous is not None:
        previous.finished_at = at
        previous.save(update_fields=["finished_at", "updated_at"])
    mount = RollMount(station=station, machine=machine, operation=step, lot=lot, roll=made,
                      mounted_by=operator, mounted_at=at)
    if lot is not None and not run.backflush:
        # Issued by the batch it is, so the run knows which roll it drew.
        mount.issue = _issue_roll(run, lot, station.warehouse, shift_date,
                                  f"Mounted on {machine.code} at {station}")
    mount.save()
    return mount


@transaction.atomic
def dismount_roll(station, operator, machine, remaining_kg=None, at=None):
    """Take a roll off; what is left of a stock roll goes back to the store."""
    from .orders import IssueDirection, MaterialIssue, MaterialIssueLine
    from .station_floor import _context, _number

    at = at or timezone.now()
    shift, shift_date = _context(station, operator, machine, at)
    mount = _open_mount(machine)
    if mount is None:
        raise ValidationError(f"Nothing is mounted on {machine.code}.")
    if remaining_kg not in (None, ""):
        left = _number(remaining_kg, "What is left")
        if mount.issue_id is None:
            raise ValidationError(f"{mount.roll_code()} was not issued from the store; there "
                                  "is nothing to return it against.")
        line = mount.issue.lines.get()
        if left > line.quantity:
            raise ValidationError(f"{left} is more than the {line.quantity} that was issued.")
        back = MaterialIssue.objects.create(
            work_order=mount.operation.work_order, direction=IssueDirection.RETURN,
            issue_date=shift_date, warehouse=mount.issue.warehouse,
            memo=f"Left on {mount.roll_code()} off {machine.code}"[:255])
        MaterialIssueLine.objects.create(issue=back, item=line.item, quantity=left,
                                         uom=line.uom, lot=line.lot, returns_line=line,
                                         line_number=1)
        back.post()
        mount.returned = back
    mount.finished_at = at
    mount.save(update_fields=["finished_at", "returned", "updated_at"])
    return mount


def _print_check(spec, registration_mm, delta_e, design_code):
    """
    A roll off the press against the sack's print: the right artwork, in
    register and on shade. Wrong artwork is refused outright — no
    supervisor makes another customer's print this one's — while
    register and shade outside tolerance are a supervisor's to take.
    """
    if not (spec.print_colours or spec.print_colours_back):
        raise ValidationError(f"{spec} is not printed; there is no print to check.")
    values = {}
    design = spec.print_design
    if design is not None:
        on_press = (design_code or "").strip()
        if on_press != design.code:
            raise ValidationError(f"{on_press or 'No design'} is on the press; {spec} is "
                                  f"printed with {design.code}.")
        values["design"] = design
    readings = []
    for value, what in ((registration_mm, "The registration error"), (delta_e, "The shade")):
        try:
            number = Decimal(str(value))
        except (ArithmeticError, TypeError, ValueError):
            raise ValidationError(f"{what} is a number.")
        if not number.is_finite() or number < 0:
            raise ValidationError(f"{what} is a number, nought or more.")
        readings.append(number)
    values["registration_mm"], values["delta_e"] = readings
    values["passed"] = (readings[0] <= spec.registration_tolerance_mm
                        and readings[1] <= spec.max_delta_e)
    return values


def _expected_added_gsm(spec):
    """What lamination adds to a square metre of fabric: coating, and any film."""
    area = spec.fabric_area_sqm()
    added = spec.lamination_grams() + spec.bopp_grams() + spec.metallic_grams()
    return added / area


@transaction.atomic
def weigh_roll(station, operator, machine, gross_kg, core_type, metres, supervisor=None,
               reason="", at=None, source="scale", typed_reason="", typed_note="",
               registration_mm=None, delta_e=None, design_code=""):
    """A laminated or printed roll off the machine, weighed and numbered."""
    from .conversion import bag_specification_for
    from .machines import Machine
    from .station import LineKind, check_supervisor
    from .station_floor import _context, _number
    from .station_gauge import typed_fields, weigh_gross

    at = at or timezone.now()
    _check_kind(station, (LineKind.COATING, LineKind.PRINTING), "weighs rolls off")
    machine = Machine.objects.select_for_update().get(pk=machine.pk)
    shift, shift_date = _context(station, operator, machine, at)
    mount = _open_mount(machine)
    if mount is None:
        raise ValidationError(f"Nothing is mounted on {machine.code}; mount the roll first.")
    run = mount.operation.work_order
    spec = bag_specification_for(run.item, shift_date)
    gross, reading = weigh_gross(station, operator, supervisor, gross_kg, source, at,
                                 typed_reason, typed_note)
    metres = _number(metres, "The metres")
    net = gross - core_type.tare_kg
    if net <= 0:
        raise ValidationError(f"{gross} kg is not more than the {core_type} core.")
    values = {}
    if station.kind == LineKind.PRINTING:
        kind = RollKind.PRINTED
        values = _print_check(spec, registration_mm, delta_e, design_code)
        off = "its print"
    else:
        off = "its lamination weight"
        if not spec.is_laminated:
            raise ValidationError(f"{spec} is not laminated; there is no lamination to weigh.")
        kind = (RollKind.BOPP if spec.bopp_film_item_id
                else RollKind.METALLIC if spec.metallic_film_item_id else RollKind.COATED)
        per_metre = mount.input_kg_per_metre()
        if per_metre is None:
            raise ValidationError(f"{mount.roll_code()} was never weighed and measured; its "
                                  "weight a metre is not known.")
        area = metres * spec.fabric_area_sqm() / (spec.cut_length_cm() / CM_PER_M)
        added = (net - per_metre * metres) * GRAMMES_PER_KG / area
        expected = _expected_added_gsm(spec)
        margin = expected * spec.lamination_tolerance_percent / Decimal("100")
        values = {"added_gsm": added.quantize(Decimal("0.001")),
                  "expected_gsm": expected.quantize(Decimal("0.001")),
                  "lower_gsm": (expected - margin).quantize(Decimal("0.001")),
                  "upper_gsm": (expected + margin).quantize(Decimal("0.001"))}
        values["passed"] = values["lower_gsm"] <= values["added_gsm"] <= values["upper_gsm"]
    reason = " ".join((reason or "").split())
    if not values["passed"]:
        check_supervisor(station, supervisor, operator)
        if not reason:
            raise ValidationError(f"Say why a roll off {off} is taken.")
        values["conceded_by"] = supervisor
        values["reason"] = reason[:255]
    count = ProcessRoll.objects.filter(machine=machine, shift=shift,
                                       shift_date=shift_date).count()
    code = (f"{PREFIX[kind]}-{shift_date:%y%m%d}-{shift.code[:1].upper()}-"
            f"{machine.code.replace('-', '').upper()}-{count + 1:02d}")
    roll = ProcessRoll.objects.create(
        code=code, kind=kind, mount=mount, station=station, machine=machine,
        core_type=core_type, gross_kg=gross, scale_reading=reading,
        **typed_fields(source, supervisor, typed_reason, typed_note), net_kg=net,
        metres=metres, weighed_by=operator, weighed_at=at, shift=shift, shift_date=shift_date,
        **values)
    return ProcessRoll.objects.get(pk=roll.pk)


@transaction.atomic
def void_mount(mount, station, supervisor, operator, reason):
    """Mounted wrongly: the issue goes back, while nothing was made from it."""
    from .station import check_supervisor

    if mount.station_id != station.pk:
        raise ValidationError(f"{mount} was not mounted at {station}.")
    if mount.voided_at is not None:
        raise ValidationError(f"{mount} is already withdrawn.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Say why the mount is withdrawn.")
    if mount.made.filter(voided_at__isnull=True).exists() or mount.bag_counts.filter(
            entry__voided_at__isnull=True).exists():
        raise ValidationError(f"Something was made from {mount}; withdraw that first.")
    check_supervisor(station, supervisor, operator)
    if mount.returned_id:
        mount.returned.void_with(mount, memo=f"Withdrawn at {station}: {reason}"[:255])
    if mount.issue_id:
        mount.issue.void_with(mount, memo=f"Withdrawn at {station}: {reason}"[:255])
    mount.voided_at, mount.voided_reason = timezone.now(), reason[:255]
    mount.save(update_fields=["voided_at", "voided_reason", "updated_at"])


@transaction.atomic
def void_roll(roll, station, supervisor, operator, reason):
    from .station import check_supervisor

    if roll.station_id != station.pk:
        raise ValidationError(f"{roll} was not weighed at {station}.")
    if roll.voided_at is not None:
        raise ValidationError(f"{roll} is already withdrawn.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Say why the roll is withdrawn.")
    if roll.mounts.filter(voided_at__isnull=True).exists():
        raise ValidationError(f"{roll} has been mounted since; withdraw that first.")
    check_supervisor(station, supervisor, operator)
    roll.voided_at, roll.voided_reason = timezone.now(), reason[:255]
    roll.save(update_fields=["voided_at", "voided_reason", "updated_at"])


def current_mount(machine):
    return RollMount.objects.filter(machine=machine, finished_at__isnull=True,
                                    voided_at__isnull=True).first()


def roll_chain(mount):
    """From a mount back to the stock roll: [mount's roll, what it came from, ...]."""
    chain = []
    seen = set()
    while mount is not None and mount.pk not in seen:
        seen.add(mount.pk)
        if mount.lot_id:
            chain.append({"code": mount.lot.code, "kind": "stock", "machine": mount.machine.code})
            break
        chain.append({"code": mount.roll.code, "kind": mount.roll.kind,
                      "machine": mount.roll.machine.code})
        mount = mount.roll.mount
    return chain

