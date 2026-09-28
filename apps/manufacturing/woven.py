"""
Woven polypropylene sacks: what the plant actually specifies.

Everything in `bom.py` is quantities of items against an item, and it
would serve a furniture factory. This is the part that knows a sack is
tape woven into a tube, and it exists because in this industry the
bill of materials is not something anybody should be typing.

A sack is sold as a specification — "50 kg capacity, 60 x 100 cm, 80
GSM, laminated, two-colour print, 10,000 pieces" — and every quantity
in its BOM follows arithmetically from that line. Type the quantities
in instead and you have stored four derived numbers per product, which
go stale the first time a customer moves the GSM and nobody remembers
which of the four to change. So the specification is the master and the
BOM is computed from it; a computed BOM refuses to be edited by hand.

The arithmetic, since it is the whole point:

- **Tape.** Denier is grammes per 9,000 metres, so 1,000-denier tape
  runs 9,000 metres to the kilo. The blend is a percentage recipe —
  filler, masterbatch, UV — and the virgin polymer is whatever is left,
  derived rather than stored so that it cannot fail to add up.

- **Fabric.** Every tape crossing a square metre contributes its own
  weight, so

      GSM = (ends/inch x warp denier + picks/inch x weft denier)
            x 39.3701 / 9000

  A 10 x 10 mesh of 1,000-denier tape gives 87.5 GSM, which is what a
  10 x 10 loom actually makes. The plant quotes a target GSM to the
  customer and sets the loom to a mesh; a mesh that cannot reach the
  quoted GSM is a specification error worth catching before the loom
  runs for three days, so the target carries a tolerance and the spec
  refuses to save outside it.

- **The sack.** Tubular fabric of lay-flat width W, cut to length L,
  is two layers, so it carries 2 x W x L square metres of fabric. At 80
  GSM a 60 x 100 cm bag with 5 cm of hem is 2 x 0.60 x 1.05 x 80 = 100.8
  grammes. Conveniently, grammes per bag is kilos per thousand bags,
  which is why the generated BOMs are written per thousand.

Every weight in here is a kilogramme. The specifications work in
grammes per bag and per square metre and hand the BOM kilogrammes, and
a plant stocking its fabric in metres or its tape in tonnes would get a
BOM reading "100 tonnes of tape from 75 kilogrammes of polymer" with
nothing to say it was wrong. So the unit is checked rather than
assumed: every weighed item in a chain must be in the same unit, that
unit must be the base of its own chain, and the sack itself must be
counted rather than weighed.

Waste is the other half. It is taken on the input at every stage, and
the part of it that comes back as regrind is a by-product rather than a
smaller input — see `bom.py` for why that distinction is not
bookkeeping pedantry.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q

from apps.core.models import AuditModel, UnitOfMeasureCategory
from apps.inventory.models import Item

from apps.quality.models import (
    Characteristic,
    CharacteristicKind,
    Evaluation,
    InspectionPlan,
    PlanLine,
    Reading,
)

from .bom import (
    BillOfMaterials,
    BomByproduct,
    BomComponent,
    ByproductValuation,
)

# Denier is grammes per nine thousand metres. Every length-to-weight
# question in this industry goes through this number.
DENIER_LENGTH_M = Decimal("9000")
INCHES_PER_METRE = Decimal("39.3701")
ONE_HUNDRED = Decimal("100")
GRAMMES_PER_KG = Decimal("1000")
CM_PER_M = Decimal("100")
# A sack is cut from a woven tube, which laid flat is two thicknesses.
# BagSpecification.save refuses flat fabric, so this is always true there.
TUBE_LAYERS = Decimal("2")
# The bottom as the trade names it: single or double fold, single or
# double stitch, or easy-open. The fold is fabric (inches: 1.25 single,
# 2 double, 0.75 easy-open without fold); the stitch is thread rows.
FOLD_TYPES = [
    ("", "Not stated"),
    ("SFSS", "Single fold, single stitch"), ("SFDS", "Single fold, double stitch"),
    ("DFSS", "Double fold, single stitch"), ("DFDS", "Double fold, double stitch"),
    ("EZWF", "Easy-open with fold"), ("EZWOF", "Easy-open without fold"),
]
# In centimetres to the two places the hem is stored at: a figure with
# more would build the bill from 3.175 and read back as 3.18.
FOLD_ALLOWANCE_CM = {
    "SFSS": Decimal("3.18"), "SFDS": Decimal("3.18"), "DFSS": Decimal("5.08"),
    "DFDS": Decimal("5.08"), "EZWF": Decimal("3.18"), "EZWOF": Decimal("1.91"),
}
BOTTOM_STITCH_ROWS = {"SFSS": 1, "SFDS": 2, "DFSS": 1, "DFDS": 2, "EZWF": 1, "EZWOF": 1}
# Chain stitch at the reference 12.5 stitches a decimetre takes 4.5
# times the seam's length in thread.
CHAIN_STITCH_FACTOR = Decimal("4.5")
REFERENCE_STITCHES_PER_DM = Decimal("12.5")

# The batch each generated BOM is written for. A thousand sacks makes
# grammes per bag and kilos per batch the same number; a hundred kilos
# of tape or fabric makes a blend percentage and a component quantity
# the same number. Both save a rounding step that would otherwise be
# taken a hundred thousand times.
TAPE_BATCH_KG = Decimal("100")
FABRIC_BATCH_KG = Decimal("100")
BAG_BATCH_PIECES = Decimal("1000")
# Grammes per square metre per micron of film: the film's density in
# grammes per cubic centimetre. BOPP is 0.91, LDPE liner film 0.92.
BOPP_GRAMS_PER_MICRON = Decimal("0.91")
LDPE_GRAMS_PER_MICRON = Decimal("0.92")


class Weave(models.TextChoices):
    TUBULAR = "tubular", "Tubular (circular loom)"
    FLAT = "flat", "Flat"


def _percent(value):
    return (value or Decimal("0")) / ONE_HUNDRED


def _measurement_unit(code, name):
    """
    The unit a characteristic is read in.

    Deliberately in the "other" category rather than in the weight or
    length chains: grammes a square metre is a measurement, not a
    quantity of stock, and letting it into the stocking graph would put
    a second root beside the kilogramme for the unit checks to trip
    over. Created on first use, the same way a document sequence is, so
    that saving a specification never fails on configuration nobody
    knew they needed.
    """
    from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory

    return UnitOfMeasure.objects.get_or_create(
        code=code,
        defaults={"name": name, "category": UnitOfMeasureCategory.OTHER},
    )[0]


def _characteristic(code, name, unit_code):
    unit = _measurement_unit(*unit_code) if unit_code else None
    characteristic, created = Characteristic.objects.get_or_create(
        code=code,
        defaults={
            "name": name,
            "kind": CharacteristicKind.MEASURED,
            "uom": unit,
        },
    )
    if not created and characteristic.uom_id is None and unit is not None:
        characteristic.uom = unit
        characteristic.save()
    return characteristic


def _six(value):
    # The places a plan line stores: a computed limit compared unrounded
    # would never equal the one read back.
    return None if value is None else Decimal(value).quantize(Decimal("0.000001"))


def _plan_row(index, row):
    code, _name, _unit, target, lower, upper, samples, rule, source = row
    return (index, code, _six(target), _six(lower), _six(upper), samples, rule, source)


def _plan_rows(plan):
    return [
        (line.line_number, line.characteristic.code, _six(line.target),
         _six(line.lower_limit), _six(line.upper_limit), line.sample_size,
         line.evaluation, line.derived_from)
        for line in plan.lines.select_related("characteristic").order_by("line_number")
    ]


def _check_weighed_in(item, unit, label, owner):
    """
    Refuse an item this arithmetic cannot be written in.

    A specification turns grammes per bag into kilogrammes per thousand,
    and that step is only true if the unit really is the kilogramme. It
    is not enough for it to be a weight: a hundred tonnes of tape from
    seventy-five kilogrammes of polymer is a weight against a weight and
    it is wrong by a factor of a thousand. So the unit must be the base
    of its chain, and every weighed item in one chain must share it.
    """
    if item.uom.category != UnitOfMeasureCategory.WEIGHT:
        raise ValidationError(
            f"{owner}: {label} {item.sku} is measured in {item.uom}, which is a "
            f"{item.uom.category}. This arithmetic is weights — grammes per "
            "square metre into kilogrammes per batch — and it cannot be written "
            "against a length or a count."
        )
    if item.uom.factor_to_root() != Decimal("1"):
        raise ValidationError(
            f"{owner}: {label} {item.sku} is measured in {item.uom}, which is "
            f"{item.uom.factor_to_root()} {item.uom.root()}. The batch sizes here "
            "are kilogrammes, so a weighed item must be stocked in the base "
            "weight unit and not a multiple of it."
        )
    if unit is not None and item.uom_id != unit.pk:
        raise ValidationError(
            f"{owner}: {label} {item.sku} is measured in {item.uom} and the rest "
            f"of this specification is in {unit}. One chain, one unit — the "
            "ratios between the stages are pure numbers and mixing units turns "
            "them into nonsense silently."
        )
    return item.uom


def _save_computed(row):
    """
    Save something a specification owns, past the guard that stops
    anybody else saving it.

    The flag is cleared afterwards whatever happens, because it lives on
    the instance and the caller keeps holding that instance: leaving it
    set once let a BOM be edited by hand for the rest of the request,
    which is the guard not working at exactly the moment it was
    supposed to.
    """
    row._rebuilding = True
    try:
        row.save()
    finally:
        row._rebuilding = False
    return row


class SpecificationWindow(models.Model):
    """
    When output made to a specification is due, passed to the bill of
    materials it builds.

    On the specification rather than on the bill because the bill is
    computed and refuses to be edited: a plant staging a new sack
    specification for the first of next month writes a second
    specification for the same item, dated from then, and closes the
    old one the day before. Each builds its own dated recipe, and an
    overlap is refused where the recipe is saved.
    """

    valid_from = models.DateField(
        null=True, blank=True,
        help_text="The first date output made to this specification is due.",
    )
    valid_to = models.DateField(
        null=True, blank=True,
        help_text="The last date output made to this specification is due.",
    )

    class Meta:
        abstract = True


class SpecificationMixin:
    """
    The part every specification shares: it owns a BOM, and the BOM is
    rebuilt whenever the specification is saved.

    Rebuilding on save rather than on a nightly job or a button is the
    same argument as every other chokepoint here. A BOM that is computed
    from a specification and is allowed to be older than it is a stored
    copy of a derived fact, which is the failure this codebase keeps
    writing down and keeps repeating.
    """

    def bom_uom(self):
        raise NotImplementedError

    def bom_batch(self):
        raise NotImplementedError

    def bom_item(self):
        raise NotImplementedError

    def bom_components(self):
        """[(item, quantity, uom, waste_percent, note)] for one batch."""
        raise NotImplementedError

    def bom_byproducts(self):
        """[(item, quantity, uom, valuation)] for one batch."""
        return []

    def bom_routing(self):
        """The machines this passes through, or None."""
        return self.routing

    def inspection_lines(self):
        """
        [(code, name, unit, target, lower, upper, samples, rule, source)]

        What a batch of this must be measured against. Derived from the
        same numbers the customer was quoted, because a hand-typed
        inspection plan is the same stored copy of a derived fact that a
        hand-typed bill of materials is, and goes stale the same way.
        """
        return []

    @transaction.atomic
    def rebuild_inspection_plan(self):
        """
        Make the plan say what the specification says.

        The plan is mandatory only where the item is tracked by batch:
        a gate that cannot say which batch it is gating is not a gate,
        and refusing to generate anything at all would leave a plant
        with no record of what it checks.
        """
        rows = self.inspection_lines()
        plan = self.inspection_plan
        if not rows:
            if plan is not None:
                type(self).objects.filter(pk=self.pk).update(inspection_plan=None)
                self.inspection_plan = None
                InspectionPlan.objects.filter(pk=plan.pk).update(is_computed=False)
            return None
        item = self.bom_item()
        if plan is None:
            plan = InspectionPlan(item=item, is_computed=True)
        plan.item = item
        plan.name = f"{self} — as specified"
        # The specification's own window: its limits apply to what is
        # made while it is in force, and a staged specification's plan
        # waits its turn beside the one it replaces.
        plan.valid_from = self.valid_from
        plan.valid_to = self.valid_to
        plan.is_mandatory = item.tracking != "none"
        # Lines already saying what the specification says are left alone:
        # a batch measured against them keeps them in place, and a save
        # that changes nothing about them, such as closing the window to
        # stage a successor, must not trip over its own history.
        unchanged = plan.pk is not None and _plan_rows(plan) == [
            _plan_row(index, row) for index, row in enumerate(rows, start=1)
        ]
        if not unchanged and plan.pk is not None and Reading.objects.filter(
                plan_line__plan=plan).exists():
            raise ValidationError(
                f"{self}: batches have been inspected against its limits, and "
                "what judged them cannot change under them. Close this "
                "specification's window and stage a new one from the day the "
                "new limits apply."
            )
        plan._rebuilding = True
        try:
            plan.save()
        finally:
            # The plan stays cached on the specification; left set, its next
            # save would pass the computed-plan guard too.
            plan._rebuilding = False
        if unchanged:
            return plan
        for row in plan.lines.all():
            row._rebuilding = True
            row.delete()
        for index, row in enumerate(rows, start=1):
            code, name, unit, target, lower, upper, samples, rule, source = row
            characteristic = _characteristic(code, name, unit)
            line = PlanLine(
                plan=plan, characteristic=characteristic, target=target,
                lower_limit=lower, upper_limit=upper, sample_size=samples,
                evaluation=rule, derived_from=source, line_number=index,
            )
            line._rebuilding = True
            line.save()
        if self.inspection_plan_id != plan.pk:
            self.inspection_plan = plan
            type(self).objects.filter(pk=self.pk).update(inspection_plan=plan)
        return plan

    @transaction.atomic
    def rebuild_bom(self):
        """
        Make the BOM say what this specification says, now.

        The BOM is replaced rather than diffed: a component the
        specification no longer produces must disappear, and a diff that
        forgets to delete is how a BOM ends up carrying a material the
        product stopped using two years ago.
        """
        bom = self.bom
        if bom is None:
            # The next free version for the item, not the default of
            # one. A second specification for one fabric — the new
            # recipe from the first of the month — was impossible while
            # an item could have only one default; with dated recipes it
            # is the normal way to stage a change, and both taking
            # version one collided on the item's version numbering.
            taken = BillOfMaterials.objects.filter(
                item=self.bom_item()
            ).aggregate(top=models.Max("version"))["top"] or 0
            bom = BillOfMaterials(
                item=self.bom_item(), name=str(self), version=taken + 1,
                quantity_produced=self.bom_batch(), uom=self.bom_uom(),
                is_computed=True,
            )
        else:
            bom.item = self.bom_item()
            bom.name = str(self)
            bom.quantity_produced = self.bom_batch()
            bom.uom = self.bom_uom()
        bom.routing = self.bom_routing()
        bom.valid_from = self.valid_from
        bom.valid_to = self.valid_to
        _save_computed(bom)
        bom.components.all().delete()
        bom.byproducts.all().delete()
        for index, row in enumerate(self.bom_components(), start=1):
            item, quantity, uom, waste, note = row
            if not quantity:
                continue
            _save_computed(BomComponent(
                bom=bom, item=item, quantity=quantity, uom=uom,
                waste_percent=waste, line_number=index, notes=note,
            ))
        for index, row in enumerate(self.bom_byproducts(), start=1):
            item, quantity, uom, valuation = row
            if not quantity:
                continue
            _save_computed(BomByproduct(
                bom=bom, item=item, quantity=quantity, uom=uom,
                valuation=valuation, line_number=index,
            ))
        if self.bom_id != bom.pk:
            self.bom = bom
            type(self).objects.filter(pk=self.pk).update(bom=bom)
        return bom

    def delete(self, *args, **kwargs):
        """
        Let the BOM go on being a BOM after its specification is gone.

        Deleting the specification and leaving the BOM marked computed
        leaves master data nobody can touch: nothing rebuilds it, because
        what rebuilt it no longer exists, and nothing may edit it,
        because it says it is computed. Releasing it costs nothing — it
        becomes an ordinary typed BOM — and it is the reverse path of
        the one this mixin's whole purpose is to build.
        """
        bom = self.bom
        result = super().delete(*args, **kwargs)
        if bom is not None:
            BillOfMaterials.objects.filter(pk=bom.pk).update(is_computed=False)
        return result

    def recovered_waste(self, output_quantity, waste_percent):
        """
        How much of a stage's loss comes back as something worth having.

        Gross input is output/(1-w), so the loss is output x w/(1-w) —
        and only the fraction the plant actually collects and reprocesses
        is a by-product. The rest is burn-off, dust and floor sweepings,
        and pretending otherwise is how a regrind account grows a balance
        nobody can find in the yard.
        """
        waste = _percent(waste_percent)
        if not waste:
            return Decimal("0")
        lost = output_quantity * waste / (Decimal("1") - waste)
        return lost * _percent(self.waste_recovered_percent)


class TapeSpecification(SpecificationMixin, SpecificationWindow, AuditModel):
    """
    What comes off the extrusion line: oriented PP tape at a denier.

    The blend is a recipe of percentages and the virgin polymer is the
    remainder, derived rather than entered, so a recipe cannot be
    written that does not add to a hundred.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, blank=True)
    tape_item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="tape_specifications",
        help_text="The tape this makes, stocked and valued by weight.",
    )
    denier = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Grammes per 9,000 metres of tape. 1,000-denier tape runs "
                  "9,000 metres to the kilo. Everything that converts between "
                  "the length a loom sees and the weight the ledger counts goes "
                  "through this number.",
    )
    tape_width_mm = models.DecimalField(
        max_digits=8, decimal_places=3,
        help_text="Slit width. With denier it fixes the stretched thickness, and "
                  "with the loom's reed it fixes whether the tapes lie flat or "
                  "ride over one another.",
    )
    draw_ratio = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="How far the tape is stretched in the orientation oven. Recorded "
                  "because it sets the tensile strength the customer specified; "
                  "nothing here computes from it.",
    )
    virgin_granule = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="+",
        help_text="The PP homopolymer. Its share of the blend is whatever the "
                  "other materials leave.",
    )
    regrind_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Reprocessed plant waste. Both an input to this blend and an "
                  "output of this process, which is what makes the material "
                  "graph cyclic and why an explosion has to carry its path.",
    )
    regrind_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("0"),
        help_text="How much of the blend is reprocessed material. Capped in "
                  "practice by what the tensile spec will bear, which is a "
                  "decision for the plant rather than a rule here.",
    )
    filler_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Calcium carbonate masterbatch.",
    )
    filler_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("0")
    )
    masterbatch_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Colour concentrate.",
    )
    masterbatch_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("0")
    )
    uv_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="UV stabiliser concentrate, for sacks that will stand in a yard.",
    )
    uv_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("0")
    )
    denier_tolerance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("5"),
        help_text="How far the tape may be from its denier before a batch of "
                  "it is out of specification. What the inspection plan "
                  "generated from this uses.",
    )
    min_tenacity_gpd = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="The least strength a batch may have, in grammes per denier. "
                  "Given, every batch is tested for it: filler is cheap and "
                  "weak, and this is where that shows.",
    )
    elongation_min_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="How far the tape must stretch before it breaks, at least.",
    )
    elongation_max_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="And at most: over-stretchy tape makes a sack that sags.",
    )
    extrusion_waste_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("3"),
        help_text="Of what is fed in: purge at start-up, edge trim, tape breaks.",
    )
    waste_recovered_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("80"),
        help_text="How much of that loss is collected and reground rather than "
                  "burnt off, swept up or lost as dust.",
    )
    routing = models.ForeignKey(
        "Routing", null=True, blank=True, on_delete=models.PROTECT,
        related_name="%(class)s_specifications",
        help_text="The machines this passes through. Named here rather than on "
                  "the bill of materials, because a computed bill refuses to "
                  "be edited and the routing is part of how the product is "
                  "made — so the specification carries it in like everything "
                  "else.",
    )
    inspection_plan = models.OneToOneField(
        "quality.InspectionPlan", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="%(class)s_specification",
        editable=False,
    )
    bom = models.OneToOneField(
        BillOfMaterials, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="tape_specification", editable=False,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                check=Q(denier__gt=0), name="tape_denier_positive"
            ),
            models.CheckConstraint(
                check=Q(tape_width_mm__gt=0), name="tape_width_positive"
            ),
            # Recovering more than was lost manufactures regrind out of
            # nothing, and the yard never sees it.
            models.CheckConstraint(
                check=Q(waste_recovered_percent__gte=0)
                & Q(waste_recovered_percent__lte=100),
                name="tape_recovery_is_a_fraction_of_the_loss",
            ),
            models.CheckConstraint(
                check=Q(extrusion_waste_percent__gte=0)
                & Q(extrusion_waste_percent__lt=100),
                name="tape_waste_under_one_hundred",
            ),
            models.CheckConstraint(
                check=Q(regrind_percent__gte=0) & Q(filler_percent__gte=0)
                & Q(masterbatch_percent__gte=0) & Q(uv_percent__gte=0),
                name="tape_blend_shares_not_negative",
            ),
        ]

    def __str__(self):
        return f"{self.code} ({self.denier} den)"

    # -- the arithmetic -------------------------------------------------

    def additive_percent(self):
        """Everything in the blend that is not virgin polymer."""
        return (
            self.regrind_percent + self.filler_percent
            + self.masterbatch_percent + self.uv_percent
        )

    def virgin_percent(self):
        """The remainder. Derived, so a recipe cannot fail to add up."""
        return ONE_HUNDRED - self.additive_percent()

    def grams_per_metre(self):
        return self.denier / DENIER_LENGTH_M

    def metres_per_kg(self):
        """9,000,000 / denier. A loom asks for metres; the ledger holds kilos."""
        return DENIER_LENGTH_M * GRAMMES_PER_KG / self.denier

    # -- the BOM it computes --------------------------------------------

    def bom_item(self):
        return self.tape_item

    def bom_batch(self):
        return TAPE_BATCH_KG

    def bom_uom(self):
        return self.tape_item.uom

    def bom_components(self):
        unit = self.virgin_granule.uom
        share = lambda percent: TAPE_BATCH_KG * _percent(percent)
        rows = [(
            self.virgin_granule, share(self.virgin_percent()), unit,
            self.extrusion_waste_percent, "Virgin PP, the balance of the blend",
        )]
        for item, percent, note in (
            (self.regrind_item, self.regrind_percent, "Reprocessed plant waste"),
            (self.filler_item, self.filler_percent, "Calcium carbonate"),
            (self.masterbatch_item, self.masterbatch_percent, "Colour"),
            (self.uv_item, self.uv_percent, "UV stabiliser"),
        ):
            if item is None or not percent:
                continue
            rows.append((
                item, share(percent), item.uom, self.extrusion_waste_percent, note
            ))
        return rows

    def inspection_lines(self):
        margin = self.denier * _percent(self.denier_tolerance_percent)
        rows = [(
            "DENIER", "Denier", ("den", "Denier"), self.denier,
            self.denier - margin, self.denier + margin, 3,
            Evaluation.MEAN, "denier",
        )]
        # Strength and stretch, where the customer set them: measured on
        # every batch, and the history the filler prediction learns from.
        if self.min_tenacity_gpd is not None:
            rows.append((
                "TENACITY", "Tenacity", ("gpd", "Grammes per denier"), None,
                self.min_tenacity_gpd, None, 5, Evaluation.MEAN, "tenacity",
            ))
        low, high = self.elongation_min_percent, self.elongation_max_percent
        if low is not None or high is not None:
            rows.append((
                "ELONG", "Elongation at break", ("%", "Per cent"),
                (low + high) / 2 if low is not None and high is not None else None,
                low, high, 5, Evaluation.MEAN, "elongation",
            ))
        return rows

    def bom_byproducts(self):
        if self.regrind_item is None:
            return []
        recovered = self.recovered_waste(
            TAPE_BATCH_KG, self.extrusion_waste_percent
        )
        return [(
            self.regrind_item, recovered, self.regrind_item.uom,
            ByproductValuation.STANDARD,
        )]

    def _check_units(self):
        unit = _check_weighed_in(self.tape_item, None, "the tape", self.code)
        for item, label in (
            (self.virgin_granule, "the virgin polymer"),
            (self.regrind_item, "the regrind"),
            (self.filler_item, "the filler"),
            (self.masterbatch_item, "the masterbatch"),
            (self.uv_item, "the UV stabiliser"),
        ):
            if item is not None:
                _check_weighed_in(item, unit, label, self.code)

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.additive_percent() >= ONE_HUNDRED:
            raise ValidationError(
                f"{self.code}: the additives come to {self.additive_percent()}% "
                "of the blend, leaving no virgin polymer. A tape is mostly "
                "polymer or it is not a tape."
            )
        if self.regrind_percent and self.regrind_item is None:
            raise ValidationError(
                f"{self.code}: the blend is {self.regrind_percent}% regrind and "
                "the specification does not say what regrind is. Name the item."
            )
        for item, percent, label in (
            (self.filler_item, self.filler_percent, "filler"),
            (self.masterbatch_item, self.masterbatch_percent, "masterbatch"),
            (self.uv_item, self.uv_percent, "UV stabiliser"),
        ):
            if percent and item is None:
                raise ValidationError(
                    f"{self.code}: the blend is {percent}% {label} and the "
                    "specification does not say which one."
                )
        self._check_strength()
        self._check_units()
        super().save(*args, **kwargs)
        self.rebuild_bom()
        self.rebuild_inspection_plan()
        self.resave_dependents()

    def _check_strength(self):
        # In save(), not clean(): specifications arrive through the API's
        # serializer and from code, and neither calls clean().
        if self.min_tenacity_gpd is not None and self.min_tenacity_gpd <= 0:
            raise ValidationError(f"{self.code}: a minimum tenacity is more than nothing.")
        low, high = self.elongation_min_percent, self.elongation_max_percent
        if any(value is not None and value <= 0 for value in (low, high)):
            raise ValidationError(f"{self.code}: elongation is more than nothing.")
        if low is not None and high is not None and low > high:
            raise ValidationError(
                f"{self.code}: elongation of at least {low}% and at most {high}% leaves "
                "nothing between."
            )

    def resave_dependents(self):
        """
        Every fabric woven from this tape, saved again against it.

        A fabric's weight is its tape's denier on a mesh, and its bill,
        its tolerance check and the sacks cut from it were all worked
        out from the denier as it was. Changing the tape and leaving
        them is a stored copy of a derived fact. A fabric the new
        denier puts outside its quotation refuses, and the tape change
        rolls back with it.
        """
        fabrics = FabricSpecification.objects.filter(
            Q(warp_tape=self) | Q(weft_tape=self)
        ).distinct()
        for fabric in fabrics:
            fabric.save()


class FabricSpecification(SpecificationMixin, SpecificationWindow, AuditModel):
    """
    What comes off the loom: tape woven at a mesh into a tube.

    GSM is derived from the mesh and the denier rather than stored,
    because that is the direction the causation runs — the loom is set
    to a mesh and the fabric then weighs what it weighs. The customer's
    number is `target_gsm`, and a mesh that cannot reach it inside the
    tolerance is refused here rather than three days into the run.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, blank=True)
    fabric_item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="fabric_specifications",
        help_text="The woven fabric this makes, stocked and valued by weight — "
                  "rolls are weighed, not measured. `metres_per_kg()` converts "
                  "for whoever is standing at the cutting table.",
    )
    warp_tape = models.ForeignKey(
        TapeSpecification, on_delete=models.PROTECT, related_name="woven_as_warp",
        help_text="The tape running along the fabric.",
    )
    weft_tape = models.ForeignKey(
        TapeSpecification, null=True, blank=True, on_delete=models.PROTECT,
        related_name="woven_as_weft",
        help_text="The tape running across it. Blank means the same tape both "
                  "ways, which is the common case; plants running a heavier warp "
                  "for strength and a lighter weft for cost say so here and get "
                  "two components instead of one.",
    )
    ends_per_inch = models.DecimalField(
        max_digits=6, decimal_places=2,
        help_text="Warp tapes per inch, measured around the tube.",
    )
    picks_per_inch = models.DecimalField(
        max_digits=6, decimal_places=2,
        help_text="Weft tapes per inch, along it.",
    )
    lay_flat_width_cm = models.DecimalField(
        max_digits=8, decimal_places=2,
        help_text="The width of the tube laid flat, which is the width of the "
                  "sack it will become.",
    )
    shrink_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("4"),
        help_text="How much finer the tape line runs than the fabric weighs. "
                  "The tape card carries the fabric's denier less this; the "
                  "tape specification is the line's denier, so the fabric "
                  "weighs the tapes laid straight divided by (1 - shrink).",
    )
    weave = models.CharField(
        max_length=8, choices=Weave.choices, default=Weave.TUBULAR
    )
    is_leno = models.BooleanField(
        default=False,
        help_text="Open mesh, the warp tapes twisted in pairs around the weft: "
                  "onion and potato sacks. It weighs what its mesh and denier "
                  "make like any fabric, and there is nothing to coat.",
    )
    target_gsm = models.DecimalField(
        max_digits=8, decimal_places=2,
        help_text="What the customer was quoted. The loom makes what the mesh "
                  "and the denier make; this is what it is supposed to make.",
    )
    gsm_tolerance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("5"),
        help_text="How far the mesh may put the fabric from the target before "
                  "the specification is wrong rather than merely approximate.",
    )
    weaving_waste_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("2"),
        help_text="Of the tape fed in: loom starts, tape breaks, selvedge.",
    )
    waste_recovered_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("85"),
        help_text="How much of that loss is collected and reground.",
    )
    loom_waste_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="What the collected loom waste is booked as — usually the same "
                  "regrind the extruder feeds on, which closes the loop.",
    )
    routing = models.ForeignKey(
        "Routing", null=True, blank=True, on_delete=models.PROTECT,
        related_name="%(class)s_specifications",
        help_text="The machines this passes through. Named here rather than on "
                  "the bill of materials, because a computed bill refuses to "
                  "be edited and the routing is part of how the product is "
                  "made — so the specification carries it in like everything "
                  "else.",
    )
    inspection_plan = models.OneToOneField(
        "quality.InspectionPlan", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="%(class)s_specification",
        editable=False,
    )
    bom = models.OneToOneField(
        BillOfMaterials, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="fabric_specification", editable=False,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                check=Q(ends_per_inch__gt=0) & Q(picks_per_inch__gt=0),
                name="fabric_mesh_positive",
            ),
            models.CheckConstraint(
                check=Q(lay_flat_width_cm__gt=0), name="fabric_width_positive"
            ),
            # A target of zero makes every tolerance check vacuous: any
            # mesh at all is then within tolerance of nothing.
            models.CheckConstraint(
                check=Q(target_gsm__gt=0), name="fabric_target_gsm_positive"
            ),
            models.CheckConstraint(
                check=Q(gsm_tolerance_percent__gte=0),
                name="fabric_tolerance_not_negative",
            ),
            models.CheckConstraint(
                check=Q(waste_recovered_percent__gte=0)
                & Q(waste_recovered_percent__lte=100),
                name="fabric_recovery_is_a_fraction_of_the_loss",
            ),
            models.CheckConstraint(
                check=Q(weaving_waste_percent__gte=0)
                & Q(weaving_waste_percent__lt=100),
                name="fabric_waste_under_one_hundred",
            ),
            models.CheckConstraint(
                check=Q(shrink_percent__gte=0) & Q(shrink_percent__lt=100),
                name="fabric_shrink_under_one_hundred",
            ),
        ]

    def __str__(self):
        return f"{self.code} ({self.gsm():.1f} GSM, {self.lay_flat_width_cm} cm)"

    # -- the arithmetic -------------------------------------------------

    def weft(self):
        """The weft tape, which is the warp tape unless it was named."""
        return self.weft_tape or self.warp_tape

    def fabric_denier(self, tape):
        """The tape's denier as it lies in the fabric, shrink taken back out."""
        return tape.denier / (Decimal("1") - _percent(self.shrink_percent))

    def warp_grams_per_sqm(self):
        return (
            self.ends_per_inch * INCHES_PER_METRE
            * self.fabric_denier(self.warp_tape) / DENIER_LENGTH_M
        )

    def weft_grams_per_sqm(self):
        return (
            self.picks_per_inch * INCHES_PER_METRE
            * self.fabric_denier(self.weft()) / DENIER_LENGTH_M
        )

    def gsm(self):
        """
        Every tape crossing a square metre weighs what its denier says.

        A 10 x 10 mesh of 1,000-denier tape comes to 87.5 GSM, which is
        what a 10 x 10 loom makes and not the round 80 a quotation tends
        to carry.
        """
        return self.warp_grams_per_sqm() + self.weft_grams_per_sqm()

    def gsm_deviation_percent(self):
        """How far the mesh puts the fabric from what was quoted."""
        if not self.target_gsm:
            return Decimal("0")
        return (self.gsm() - self.target_gsm) / self.target_gsm * ONE_HUNDRED

    def layers(self):
        """A tube laid flat is two thicknesses of fabric; flat cloth is one."""
        return Decimal("2") if self.weave == Weave.TUBULAR else Decimal("1")

    def grams_per_metre(self):
        """One running metre of the roll, at its lay-flat width."""
        return self.gsm() * (self.lay_flat_width_cm / CM_PER_M) * self.layers()

    def metres_per_kg(self):
        """For the cutting table, which thinks in metres and is handed kilos."""
        return GRAMMES_PER_KG / self.grams_per_metre()

    def warp_share(self):
        """What fraction of the fabric's weight the warp tape is."""
        total = self.gsm()
        return self.warp_grams_per_sqm() / total if total else Decimal("0")

    # -- the BOM it computes --------------------------------------------

    def bom_item(self):
        return self.fabric_item

    def bom_batch(self):
        return FABRIC_BATCH_KG

    def bom_uom(self):
        return self.fabric_item.uom

    def bom_components(self):
        warp_share = self.warp_share()
        if self.weft_tape_id is None or self.weft_tape_id == self.warp_tape_id:
            return [(
                self.warp_tape.tape_item, FABRIC_BATCH_KG,
                self.warp_tape.tape_item.uom, self.weaving_waste_percent,
                "Tape, warp and weft",
            )]
        return [
            (
                self.warp_tape.tape_item, FABRIC_BATCH_KG * warp_share,
                self.warp_tape.tape_item.uom, self.weaving_waste_percent, "Warp tape",
            ),
            (
                self.weft().tape_item, FABRIC_BATCH_KG * (Decimal("1") - warp_share),
                self.weft().tape_item.uom, self.weaving_waste_percent, "Weft tape",
            ),
        ]

    def inspection_lines(self):
        # Against what the customer was QUOTED, not against what the
        # mesh makes. The loom's own figure is already checked when the
        # specification saves; this is the promise the goods are sold on.
        margin = self.target_gsm * _percent(self.gsm_tolerance_percent)
        return [(
            "GSM", "Grammes per square metre", ("gsm", "Grammes per square metre"),
            self.target_gsm, self.target_gsm - margin, self.target_gsm + margin,
            3, Evaluation.MEAN, "gsm",
        )]

    def bom_byproducts(self):
        if self.loom_waste_item is None:
            return []
        recovered = self.recovered_waste(
            FABRIC_BATCH_KG, self.weaving_waste_percent
        )
        return [(
            self.loom_waste_item, recovered, self.loom_waste_item.uom,
            ByproductValuation.STANDARD,
        )]

    def _check_units(self):
        unit = _check_weighed_in(self.fabric_item, None, "the fabric", self.code)
        _check_weighed_in(
            self.warp_tape.tape_item, unit, "the warp tape", self.code
        )
        _check_weighed_in(self.weft().tape_item, unit, "the weft tape", self.code)
        if self.loom_waste_item is not None:
            _check_weighed_in(
                self.loom_waste_item, unit, "the loom waste", self.code
            )

    @transaction.atomic
    def save(self, *args, **kwargs):
        self._check_units()
        deviation = abs(self.gsm_deviation_percent())
        if deviation > self.gsm_tolerance_percent:
            raise ValidationError(
                f"{self.code}: a {self.ends_per_inch} x {self.picks_per_inch} mesh "
                f"of {self.warp_tape.denier} denier tape at {self.shrink_percent}% "
                f"shrink makes {self.gsm():.1f} GSM, which is {deviation:.1f}% from the {self.target_gsm} GSM "
                f"quoted and outside the {self.gsm_tolerance_percent}% tolerance. "
                "Change the mesh, the denier or the quotation — the loom will not "
                "split the difference."
            )
        super().save(*args, **kwargs)
        self.rebuild_bom()
        self.rebuild_inspection_plan()
        # The sacks cut from this fabric were weighed against it as it
        # was. Each is saved again, and one that no longer meets its
        # contracted weight refuses the change to the fabric.
        for bag in self.bags.all():
            bag.save()


class BagSpecification(SpecificationMixin, SpecificationWindow, AuditModel):
    """
    The sack as the customer ordered it, and everything that follows.

    "50 kg capacity, 60 x 100 cm, 80 GSM, laminated, two colours,
    10,000 pieces" is a complete specification, and every quantity in
    the BOM under it is arithmetic on that line. The BOM is written per
    thousand bags because grammes per bag is then kilos per batch, and
    the multiplication that would otherwise be done a hundred thousand
    times with a four-place rounding in it is not done at all.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, blank=True)
    bag_item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="bag_specifications",
        help_text="The finished sack, counted in pieces.",
    )
    fabric = models.ForeignKey(
        FabricSpecification, on_delete=models.PROTECT, related_name="bags"
    )
    bag_width_cm = models.DecimalField(
        max_digits=8, decimal_places=2,
        help_text="The finished sack's width, which is the fabric's lay-flat "
                  "width: the tube is woven at the width of the bag it becomes. "
                  "On a gusseted sack this is the whole width, gussets folded "
                  "inside it, the way the trade quotes it.",
    )
    bag_length_cm = models.DecimalField(
        max_digits=8, decimal_places=2,
        help_text="The finished sack's length, hems excluded.",
    )
    gusset_cm = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="How deep each side's gusset folds in. A 28-inch sack with "
                  "2-inch gussets is a 28-inch tube with a 24-inch printed face: "
                  "the gusset moves fabric from the face to the side, and adds "
                  "none.",
    )
    fold_type = models.CharField(
        max_length=8, choices=FOLD_TYPES, blank=True, default="",
        help_text="How the bottom is folded and stitched. Sets the stitch rows "
                  "a computed thread is worked from, and offers the fold's "
                  "fabric allowance when none is given.",
    )
    bottom_hem_cm = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("3"),
        help_text="Fabric turned up and stitched at the bottom. Real fabric, "
                  "cut and paid for, and left out of more than one costing.",
    )
    top_hem_cm = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("2"),
        help_text="Fabric folded and hemmed at the mouth, one stitch row; "
                  "nought for a raw mouth. On a block-bottom valve sack, the "
                  "top end's fold allowance.",
    )
    closure = models.CharField(
        max_length=8, default="sewn",
        choices=[("sewn", "Sewn"), ("welded", "Welded (block bottom)")],
        help_text="Sewn with thread, or folded into a block bottom and welded "
                  "with hot air, as valve sacks for cement are. A welded sack "
                  "has no thread and must be coated: the coating is what melts.",
    )
    is_laminated = models.BooleanField(default=False)
    lamination_gsm = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="Weight of the coating per square metre of fabric, typically "
                  "12 to 20. Applied over the same area the fabric covers.",
    )
    lamination_tolerance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("10"),
        help_text="How far the coating weighed off the coater may sit from its "
                  "GSM. Over it is polymer given away; under it, a sack that "
                  "sifts or will not weld.",
    )
    lamination_waste_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("4"),
        help_text="Of the coating polymer fed in. A coating line's own loss, "
                  "separate from what cutting and stitching waste.",
    )
    bopp_film_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Printed BOPP film laminated over the fabric. It is bonded by "
                  "the extruded coating, so a BOPP sack is also laminated.",
    )
    bopp_micron = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="Film thickness; 18 to 25 is usual. Covers the whole outer "
                  "fabric area.",
    )
    bopp_faces = models.PositiveSmallIntegerField(
        default=2, choices=[(1, "One face (SS)"), (2, "Both faces (BS)")],
        help_text="Film laminated on one face of the flattened tube or on both.",
    )
    bopp_waste_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("4"),
        help_text="Of the film fed in: registration, trim and splices.",
    )
    print_colours = models.PositiveSmallIntegerField(
        default=0, help_text="Colours printed on the front face.",
    )
    print_colours_back = models.PositiveSmallIntegerField(
        default=0, help_text="Colours printed on the back face; often fewer.",
    )
    ink_grams_per_sqm_per_colour = models.DecimalField(
        max_digits=8, decimal_places=3, default=Decimal("0.5"),
        help_text="Ink laid down per square metre per colour. Small, and the "
                  "only reason it is here is that a plant printing four colours "
                  "on a million sacks is buying ink by the tonne.",
    )
    ink_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    thread_grams_per_bag = models.DecimalField(
        max_digits=8, decimal_places=3, default=Decimal("0"),
        help_text="Sewing thread, typed. Leave at nought to compute it from "
                  "the thread's denier and the stitch rows.",
    )
    thread_denier = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0"),
        help_text="The sewing yarn's denier, to compute the thread from the "
                  "seams: each row is the sack's width in chain stitch.",
    )
    stitches_per_dm = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("12.5"),
        help_text="Stitch density; more stitches, more thread.",
    )
    thread_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reducer_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Thins the ink on the press and evaporates: bought and used, "
                  "never in the sack.",
    )
    reducer_percent = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="Of the ink's weight.",
    )
    solvent_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="MIBK or another press solvent. Like the reducer, used and gone.",
    )
    solvent_percent = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="Of the ink's weight.",
    )
    valve_patch_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="The valve a valve sack is filled through.",
    )
    valve_patch_grams = models.DecimalField(max_digits=8, decimal_places=3,
                                            default=Decimal("0"))
    cover_patch_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="The cover sheets welded over a block bottom's folds.",
    )
    cover_patch_grams = models.DecimalField(
        max_digits=8, decimal_places=3, default=Decimal("0"),
        help_text="Both ends together.",
    )
    handle_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="A handle strip or loop sewn to the sack.",
    )
    handle_grams = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("0"))
    dcut_area_sqcm = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0"),
        help_text="The D-cut handle's hole, on one face. It is punched through "
                  "both, fabric, coating and film with it: bought, cut, and "
                  "not in the sack.",
    )
    metallic_film_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Metallised film on the front face, bonded by the coating.",
    )
    metallic_micron = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    metallic_coverage_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("100"),
        help_text="Of the face the film covers: 100 for full metallic, less "
                  "for a window film with the product showing through.",
    )
    liner_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="An inner LDPE liner, for sacks that must keep moisture out.",
    )
    liner_grams_per_bag = models.DecimalField(
        max_digits=8, decimal_places=3, default=Decimal("0"),
        help_text="Typed, where the liner is bought or weighed as a finished "
                  "piece. Leave at nought to compute it from the film below.",
    )
    liner_micron = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="Liner film thickness, to compute its weight from its size.",
    )
    liner_width_cm = models.DecimalField(max_digits=8, decimal_places=2,
                                         default=Decimal("0"))
    liner_length_cm = models.DecimalField(max_digits=8, decimal_places=2,
                                          default=Decimal("0"))
    conversion_waste_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("2.5"),
        help_text="Of the fabric fed into cutting and stitching: the offcut at "
                  "each end of the roll, rejects, mis-stitched bags.",
    )
    waste_recovered_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("70"),
        help_text="How much of that offcut is collected and reground. Lower than "
                  "upstream, because a printed laminated offcut is harder to "
                  "reprocess than clean tape waste.",
    )
    cutting_waste_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    routing = models.ForeignKey(
        "Routing", null=True, blank=True, on_delete=models.PROTECT,
        related_name="%(class)s_specifications",
        help_text="The machines this passes through. Named here rather than on "
                  "the bill of materials, because a computed bill refuses to "
                  "be edited and the routing is part of how the product is "
                  "made — so the specification carries it in like everything "
                  "else.",
    )
    target_grams = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True,
        help_text="The weight the customer contracted for. When given, the "
                  "sack this specification computes must fall within the "
                  "tolerance of it, and the weight check inspects against it.",
    )
    weight_tolerance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("5"),
        help_text="How far a finished sack may be from its contracted weight, "
                  "or its computed one when none was contracted. The number a "
                  "customer's own goods-in scale argues about.",
    )
    inspection_plan = models.OneToOneField(
        "quality.InspectionPlan", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="%(class)s_specification",
        editable=False,
    )
    bom = models.OneToOneField(
        BillOfMaterials, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bag_specification", editable=False,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                check=Q(bag_width_cm__gt=0) & Q(bag_length_cm__gt=0),
                name="bag_dimensions_positive",
            ),
            # A coating with no weight is not a coating, and a weight with
            # no coating silently adds polymer to an unlaminated sack.
            models.CheckConstraint(
                check=Q(is_laminated=True) | Q(lamination_gsm=0),
                name="unlaminated_bag_has_no_coating_weight",
            ),
            models.CheckConstraint(
                check=Q(waste_recovered_percent__gte=0)
                & Q(waste_recovered_percent__lte=100),
                name="bag_recovery_is_a_fraction_of_the_loss",
            ),
            models.CheckConstraint(
                check=Q(conversion_waste_percent__gte=0)
                & Q(conversion_waste_percent__lt=100)
                & Q(lamination_waste_percent__gte=0)
                & Q(lamination_waste_percent__lt=100),
                name="bag_waste_under_one_hundred",
            ),
            models.CheckConstraint(
                check=Q(bottom_hem_cm__gte=0) & Q(top_hem_cm__gte=0),
                name="bag_hems_not_negative",
            ),
            models.CheckConstraint(
                check=Q(lamination_tolerance_percent__gte=0),
                name="bag_coating_tolerance_not_negative",
            ),
            models.CheckConstraint(
                check=Q(gusset_cm__gte=0) & Q(bopp_micron__gte=0)
                & Q(valve_patch_grams__gte=0) & Q(cover_patch_grams__gte=0)
                & Q(liner_micron__gte=0) & Q(liner_width_cm__gte=0)
                & Q(liner_length_cm__gte=0) & Q(liner_grams_per_bag__gte=0)
                & Q(thread_grams_per_bag__gte=0),
                name="bag_quantities_not_negative",
            ),
            models.CheckConstraint(
                check=Q(thread_denier__gte=0) & Q(stitches_per_dm__gt=0)
                & Q(reducer_percent__gte=0) & Q(solvent_percent__gte=0),
                name="bag_thread_and_solvents_not_negative",
            ),
            models.CheckConstraint(
                check=Q(fold_type__in=[code for code, _ in FOLD_TYPES]),
                name="bag_fold_type_known",
            ),
            models.CheckConstraint(
                check=Q(handle_grams__gte=0) & Q(dcut_area_sqcm__gte=0)
                & Q(metallic_micron__gte=0) & Q(metallic_coverage_percent__gt=0)
                & Q(metallic_coverage_percent__lte=100),
                name="bag_handle_cut_and_metallic_in_range",
            ),
            models.CheckConstraint(
                check=Q(bopp_waste_percent__gte=0) & Q(bopp_waste_percent__lt=100),
                name="bag_film_waste_under_one_hundred",
            ),
            models.CheckConstraint(
                check=Q(closure__in=["sewn", "welded"]), name="bag_closure_known",
            ),
            models.CheckConstraint(
                check=Q(bag_width_cm__gt=F("gusset_cm") * 2), name="bag_gussets_leave_a_face",
            ),
            models.CheckConstraint(
                check=Q(bopp_faces__in=[1, 2]), name="bag_bopp_faces_known",
            ),
            models.CheckConstraint(
                check=Q(target_grams__isnull=True) | Q(target_grams__gt=0),
                name="bag_target_weight_positive",
            ),
            models.CheckConstraint(
                check=Q(weight_tolerance_percent__gte=0),
                name="bag_weight_tolerance_not_negative",
            ),
        ]

    def __str__(self):
        return (
            f"{self.code} ({self.bag_width_cm} x {self.bag_length_cm} cm, "
            f"{self.fabric.gsm():.0f} GSM)"
        )

    # -- the arithmetic -------------------------------------------------

    def cut_length_cm(self):
        """What is actually cut off the roll, hems included."""
        return self.bag_length_cm + self.bottom_hem_cm + self.top_hem_cm

    def fabric_area_sqm(self):
        """
        Single-layer fabric in one sack.

        A tube laid flat is two thicknesses, so a 60 cm sack cut at 105
        cm carries 2 x 0.60 x 1.05 square metres — not 0.63. Gussets fold
        inside that width, so they change the face and not the fabric.
        """
        return (
            TUBE_LAYERS
            * (self.bag_width_cm / CM_PER_M)
            * (self.cut_length_cm() / CM_PER_M)
        )

    def face_width_cm(self):
        """What shows from the front once the gussets are folded in."""
        return self.bag_width_cm - 2 * self.gusset_cm

    def face_area_sqm(self):
        return (self.face_width_cm() / CM_PER_M) * (self.bag_length_cm / CM_PER_M)

    def printed_area_sqm(self):
        """The faces that carry any print."""
        faces = (1 if self.print_colours else 0) + (1 if self.print_colours_back else 0)
        return Decimal(faces) * self.face_area_sqm()

    def stitch_rows(self):
        """The bottom's rows by its fold, and one more for a hemmed mouth."""
        return BOTTOM_STITCH_ROWS.get(self.fold_type, 0) + (1 if self.top_hem_cm else 0)

    def thread_grams(self):
        """Typed, or each stitch row as a width of chain stitch in the yarn."""
        if not self.thread_denier:
            return self.thread_grams_per_bag
        per_row = (
            (self.bag_width_cm / CM_PER_M)
            * (self.stitches_per_dm / REFERENCE_STITCHES_PER_DM)
            * CHAIN_STITCH_FACTOR * self.thread_denier / DENIER_LENGTH_M
        )
        return per_row * self.stitch_rows()

    def fabric_grams(self):
        return self.fabric_area_sqm() * self.fabric.gsm()

    def lamination_grams(self):
        if not self.is_laminated:
            return Decimal("0")
        return self.fabric_area_sqm() * self.lamination_gsm

    def bopp_grams(self):
        """
        Film over one face of the flattened tube or both. Its density is
        0.91: a micron over a square metre is a cubic centimetre, and
        BOPP weighs 0.91 g of it, as LDPE weighs 0.92.
        """
        if not self.bopp_micron:
            return Decimal("0")
        share = Decimal(self.bopp_faces) / 2
        return self.fabric_area_sqm() * share * self.bopp_micron * BOPP_GRAMS_PER_MICRON

    def metallic_grams(self):
        """Film over the front face, or the share of it a window leaves."""
        if not self.metallic_micron:
            return Decimal("0")
        return (
            self.fabric_area_sqm() / TUBE_LAYERS * _percent(self.metallic_coverage_percent)
            * self.metallic_micron * BOPP_GRAMS_PER_MICRON
        )

    def punched_area_sqm(self):
        """The D-cut through both faces: one layer of fabric on each."""
        return TUBE_LAYERS * self.dcut_area_sqcm / (CM_PER_M * CM_PER_M)

    def punched_addon_grams(self):
        """Coating and film that go out with the D-cut."""
        hole = self.dcut_area_sqcm / (CM_PER_M * CM_PER_M)
        if not hole:
            return Decimal("0")
        coat = TUBE_LAYERS * hole * self.lamination_gsm if self.is_laminated else Decimal("0")
        film = hole * Decimal(self.bopp_faces) * self.bopp_micron * BOPP_GRAMS_PER_MICRON
        metallic = (hole * _percent(self.metallic_coverage_percent)
                    * self.metallic_micron * BOPP_GRAMS_PER_MICRON)
        return coat + film + metallic

    def punched_grams(self):
        """All of it: waste the plant bought, cut and can partly recover."""
        return self.punched_area_sqm() * self.fabric.gsm() + self.punched_addon_grams()

    def liner_grams(self):
        """Typed, or two layers of film at the liner's size and thickness."""
        if self.liner_micron:
            return (
                2 * (self.liner_width_cm / CM_PER_M) * (self.liner_length_cm / CM_PER_M)
                * self.liner_micron * LDPE_GRAMS_PER_MICRON
            )
        return self.liner_grams_per_bag

    def construction(self):
        """What kind of sack this is, in the words the trade uses."""
        parts = []
        if self.bopp_film_item_id:
            parts.append("BOPP laminated")
        elif self.is_laminated:
            parts.append("laminated")
        elif self.fabric.is_leno:
            parts.append("leno")
        else:
            parts.append("unlaminated")
        if self.metallic_film_item_id:
            parts.append("metallic" if self.metallic_coverage_percent == 100 else "metallic window")
        if self.gusset_cm:
            parts.append("gusseted")
        if self.valve_patch_item_id:
            parts.append("valve")
        if self.closure == "welded":
            parts.append("block bottom")
        if self.dcut_area_sqcm:
            parts.append("D-cut")
        if self.handle_item_id:
            parts.append("with handle")
        if self.liner_item_id:
            parts.append("with liner")
        return ", ".join(parts)

    def ink_grams(self):
        """Each colour on each face it is printed on, dry."""
        colours = self.print_colours + self.print_colours_back
        return self.face_area_sqm() * self.ink_grams_per_sqm_per_colour * Decimal(colours)

    def bag_grams(self):
        """
        What one finished sack weighs.

        The number a customer checks on a weighbridge and the number a
        costing is built on, and they had better be the same one.
        """
        # The fabric bought is all of fabric_grams(); the D-cut takes
        # some of it back out of the sack.
        fabric_in_sack = self.fabric_area_sqm() - self.punched_area_sqm()
        return fabric_in_sack * self.fabric.gsm() + self.addon_grams()

    def addon_grams(self):
        """Everything in the finished sack that is not the woven fabric."""
        return (
            self.lamination_grams() + self.bopp_grams() + self.metallic_grams()
            + self.ink_grams() + self.thread_grams() + self.liner_grams()
            + self.valve_patch_grams + self.cover_patch_grams + self.handle_grams
            - self.punched_addon_grams()
        )

    def fabric_gsm_for(self, target_grams):
        """
        The fabric a sack of this construction needs to weigh `target_grams`.

        Linear, because every add-on is fixed by the construction and the
        fabric is the only weight left to choose: what the customer
        contracted, less the add-ons, over the fabric in the sack.
        """
        target = Decimal(target_grams)
        addons = self.addon_grams()
        if addons >= target:
            raise ValidationError(
                f"The sack without its fabric already weighs {addons:.2f} g, "
                f"so no fabric makes it {target} g. Raise the weight or take "
                "something off."
            )
        return (target - addons) / (self.fabric_area_sqm() - self.punched_area_sqm())

    def fabric_metres_per_bag(self):
        """What the cutting table sets the machine to."""
        return self.cut_length_cm() / CM_PER_M

    # -- the BOM it computes --------------------------------------------

    def bom_item(self):
        return self.bag_item

    def bom_batch(self):
        return BAG_BATCH_PIECES

    def bom_uom(self):
        return self.bag_item.uom

    def bom_components(self):
        # Grammes in one bag are kilos in a thousand of them, which is
        # why the batch is a thousand.
        rows = [(
            self.fabric.fabric_item, self.fabric_grams(),
            self.fabric.fabric_item.uom, self.conversion_waste_percent,
            f"Fabric, {self.fabric_metres_per_bag():.3f} m per bag at "
            f"{self.fabric.lay_flat_width_cm} cm",
        )]
        blend = self.coating_blend()
        total = sum(parts for _, parts in blend)
        for item, parts in blend:
            rows.append((
                item, self.lamination_grams() * parts / total,
                item.uom, self.lamination_waste_percent,
                f"Coating, {parts / total * ONE_HUNDRED:.1f}% of {self.lamination_gsm} GSM",
            ))
        if self.bopp_film_item is not None:
            rows.append((
                self.bopp_film_item, self.bopp_grams(), self.bopp_film_item.uom,
                self.bopp_waste_percent, f"BOPP film, {self.bopp_micron} micron",
            ))
        if self.ink_item is not None:
            rows.append((
                self.ink_item, self.ink_grams(), self.ink_item.uom,
                self.conversion_waste_percent,
                f"Ink, {self.print_colours} front and {self.print_colours_back} back",
            ))
        # Solvents are bought by the ink they thin and leave nothing in
        # the sack, so they are in the bill and not in bag_grams().
        for item, percent, label in (
            (self.reducer_item, self.reducer_percent, "Reducer"),
            (self.solvent_item, self.solvent_percent, "Solvent"),
        ):
            if item is not None:
                rows.append((
                    item, self.ink_grams() * _percent(percent), item.uom,
                    self.conversion_waste_percent, f"{label}, {percent}% of the ink",
                ))
        if self.thread_item is not None:
            rows.append((
                self.thread_item, self.thread_grams(),
                self.thread_item.uom, self.conversion_waste_percent,
                "Sewing thread",
            ))
        if self.metallic_film_item is not None:
            rows.append((
                self.metallic_film_item, self.metallic_grams(), self.metallic_film_item.uom,
                self.bopp_waste_percent,
                f"Metallic film, {self.metallic_micron} micron, "
                f"{self.metallic_coverage_percent}% of the face",
            ))
        if self.handle_item is not None:
            rows.append((
                self.handle_item, self.handle_grams, self.handle_item.uom,
                self.conversion_waste_percent, "Handle",
            ))
        if self.liner_item is not None:
            rows.append((
                self.liner_item, self.liner_grams(),
                self.liner_item.uom, self.conversion_waste_percent, "Inner liner",
            ))
        if self.valve_patch_item is not None:
            rows.append((
                self.valve_patch_item, self.valve_patch_grams,
                self.valve_patch_item.uom, self.conversion_waste_percent, "Valve",
            ))
        if self.cover_patch_item is not None:
            rows.append((
                self.cover_patch_item, self.cover_patch_grams,
                self.cover_patch_item.uom, self.conversion_waste_percent,
                "Block-bottom cover sheets",
            ))
        return rows

    def weight_deviation_percent(self):
        """How far the computed sack is from the contracted one, if any."""
        if self.target_grams is None:
            return None
        return (self.bag_grams() - self.target_grams) / self.target_grams * ONE_HUNDRED

    def inspection_lines(self):
        # Against the contract where there is one: that is the number
        # the goods are sold on and the customer's scale checks.
        weight = self.target_grams if self.target_grams is not None else self.bag_grams()
        margin = weight * _percent(self.weight_tolerance_percent)
        return [(
            "BAGWT", "Finished bag weight", ("g-bag", "Grammes a bag"),
            weight, weight - margin, weight + margin, 10,
            Evaluation.MEAN, "bag_weight",
        )]

    def bom_byproducts(self):
        if self.cutting_waste_item is None:
            return []
        recovered = self.recovered_waste(
            self.fabric_grams(), self.conversion_waste_percent
        ) + self.punched_grams() * _percent(self.waste_recovered_percent)
        return [(
            self.cutting_waste_item, recovered, self.cutting_waste_item.uom,
            ByproductValuation.STANDARD,
        )]

    def _check_units(self):
        if self.bag_item.uom.category != UnitOfMeasureCategory.COUNT:
            raise ValidationError(
                f"{self.code}: sacks are counted, and {self.bag_item.sku} is "
                f"measured in {self.bag_item.uom}. A BOM written per thousand "
                "pieces against an item stocked by weight reads as a thousand "
                "kilogrammes of sacks."
            )
        unit = self.fabric.fabric_item.uom
        for item, label in (
            *((item, "the coating") for item, _ in self.coating_blend()),
            (self.ink_item, "the ink"),
            (self.thread_item, "the thread"),
            (self.reducer_item, "the reducer"),
            (self.solvent_item, "the solvent"),
            (self.liner_item, "the liner"),
            (self.bopp_film_item, "the BOPP film"),
            (self.valve_patch_item, "the valve"),
            (self.cover_patch_item, "the cover sheets"),
            (self.handle_item, "the handle"),
            (self.metallic_film_item, "the metallic film"),
            (self.cutting_waste_item, "the cutting waste"),
        ):
            if item is not None:
                _check_weighed_in(item, unit, label, self.code)

    @transaction.atomic
    def save(self, *args, **kwargs):
        self._check_units()
        if self.fabric.weave != Weave.TUBULAR:
            raise ValidationError(
                f"{self.code}: {self.fabric.code} is flat fabric. This "
                "specification computes a sack cut from a woven tube, where the "
                "lay-flat width is the sack's width and there is no side seam. "
                "A sack made from flat fabric is a different construction and "
                "needs its own arithmetic, not this one bent to fit."
            )
        if self.fabric.lay_flat_width_cm != self.bag_width_cm:
            raise ValidationError(
                f"{self.code}: the sack is {self.bag_width_cm} cm wide and "
                f"{self.fabric.code} is woven {self.fabric.lay_flat_width_cm} cm "
                "lay-flat. The tube is the sack's width — one of the two is wrong."
            )
        self._check_construction()
        self._check_coating()
        colours = self.print_colours + self.print_colours_back
        if colours and self.ink_item is None:
            raise ValidationError(
                f"{self.code}: the sack is printed in {colours} "
                "colour(s) and the specification does not say what with."
            )
        deviation = self.weight_deviation_percent()
        if deviation is not None and abs(deviation) > self.weight_tolerance_percent:
            raise ValidationError(
                f"{self.code}: the sack as specified weighs "
                f"{self.bag_grams():.2f} g, which is {abs(deviation):.1f}% from "
                f"the {self.target_grams} g contracted and outside the "
                f"{self.weight_tolerance_percent}% tolerance. The fabric, the "
                "add-ons or the contract has to change."
            )
        super().save(*args, **kwargs)
        if getattr(self, "_coating", None) is not None:
            self.coating_lines.all().delete()
            for item, parts in self._coating:
                line = BagCoatingLine(specification=self, item=item, parts=parts)
                line._via_specification = True
                line.save()
            self._coating = None
        self.rebuild_bom()
        self.rebuild_inspection_plan()

    # -- the coating blend ----------------------------------------------

    def set_coating(self, blend):
        """
        The coating as [(item, parts)], parts relative: 80 and 20 is the
        same blend as 4 and 1. Held until save(), which checks it,
        writes it and builds the bill from it in one transaction, so a
        laminated sack is never saved with a coating it does not have.
        """
        self._coating = [(item, Decimal(parts)) for item, parts in blend]

    def coating_blend(self):
        if getattr(self, "_coating", None) is not None:
            return self._coating
        if self.pk is None:
            return []
        return [(line.item, line.parts)
                for line in self.coating_lines.select_related("item__uom").order_by("id")]

    def _check_coating(self):
        blend = self.coating_blend()
        if self.is_laminated and not blend:
            raise ValidationError(
                f"{self.code}: the sack is laminated and the specification does "
                "not say what with."
            )
        if blend and not self.is_laminated:
            raise ValidationError(
                f"{self.code}: a coating blend on a sack that is not laminated "
                "would be bought for every sack and put on none."
            )
        seen = set()
        for item, parts in blend:
            if parts <= 0:
                raise ValidationError(
                    f"{self.code}: {item.sku} is in the coating at {parts} parts; "
                    "a share of the blend is more than nothing."
                )
            if item.pk in seen:
                raise ValidationError(
                    f"{self.code}: {item.sku} is in the coating twice. Give it once, "
                    "with its whole share."
                )
            seen.add(item.pk)

    def _check_shape(self):
        """
        What the sack's own figures rule out, before any material is
        named — so an enquiry that has no items yet is held to it too.
        """
        lead = f"{self.code}: " if self.code else ""
        if 2 * self.gusset_cm >= self.bag_width_cm:
            raise ValidationError(
                f"{lead}{self.gusset_cm} cm gussets fold in from both sides of "
                f"a {self.bag_width_cm} cm sack and leave no face."
            )
        if (self.bopp_micron or self.metallic_micron) and not self.is_laminated:
            raise ValidationError(
                f"{lead}film is bonded by the extruded coating. Set the "
                "lamination it is laminated with."
            )
        hole = self.dcut_area_sqcm / (CM_PER_M * CM_PER_M)
        if hole and hole >= self.face_area_sqm():
            raise ValidationError(
                f"{lead}a {self.dcut_area_sqcm} cm2 D-cut is as big as the "
                "sack's face; there would be no sack left around it."
            )
        if self.thread_denier:
            if self.thread_grams_per_bag:
                raise ValidationError(
                    f"{lead}the thread is typed at {self.thread_grams_per_bag} g "
                    "and computed from its denier too. Give one or the other."
                )
            if not self.fold_type:
                raise ValidationError(
                    f"{lead}a thread computed from its denier needs the fold type: "
                    "the stitch rows follow from it."
                )
        if (self.reducer_percent or self.solvent_percent) and not (
            self.print_colours or self.print_colours_back
        ):
            raise ValidationError(
                f"{lead}the sack is not printed, so there is no ink for a reducer "
                "or solvent to thin."
            )
        if self.liner_micron:
            if self.liner_grams_per_bag:
                raise ValidationError(
                    f"{lead}the liner is typed at {self.liner_grams_per_bag} g "
                    "and computed from its film too. Give one or the other."
                )
            if not (self.liner_width_cm and self.liner_length_cm):
                raise ValidationError(
                    f"{lead}a liner computed from its film needs its width and "
                    "length."
                )

    def _check_construction(self):
        """
        The combinations a sack cannot be. Each refusal is a material the
        costing would otherwise count or miss: film with nothing to hold
        it, thread in a sack that has none, a weight nobody buys.
        """
        code = self.code
        self._check_shape()
        if (self.bopp_film_item_id is None) != (not self.bopp_micron):
            raise ValidationError(
                f"{code}: BOPP film needs both the film and its thickness; one "
                "without the other is film nobody can weigh."
            )
        if self.closure == "welded":
            if not self.is_laminated:
                raise ValidationError(
                    f"{code}: a welded block bottom melts the coating together; "
                    "an uncoated sack has nothing to weld."
                )
            if self.thread_item_id or self.thread_grams_per_bag or self.thread_denier:
                raise ValidationError(
                    f"{code}: a welded sack is not sewn. Take the thread off, or "
                    "it is costed into every sack and used in none."
                )
        elif (self.thread_item_id is None) != (not self.thread_grams()):
            raise ValidationError(
                f"{code}: the thread needs both an item and a weight (typed, or "
                "a denier and a fold to work it from)."
            )
        if self.fabric.is_leno:
            for present, what in (
                (self.is_laminated, "coated"), (self.handle_item_id or self.handle_grams, "given a handle"),
            ):
                if present:
                    raise ValidationError(
                        f"{code}: {self.fabric.code} is leno, an open mesh, and "
                        f"cannot be {what}."
                    )
        if (self.metallic_film_item_id is None) != (not self.metallic_micron):
            raise ValidationError(
                f"{code}: metallic film needs both the film and its thickness."
            )
        for item, grams, label in (
            (self.handle_item_id, self.handle_grams, "handle"),
            (self.valve_patch_item_id, self.valve_patch_grams, "valve"),
            (self.cover_patch_item_id, self.cover_patch_grams, "cover sheets"),
            (self.reducer_item_id, self.reducer_percent, "reducer"),
            (self.solvent_item_id, self.solvent_percent, "solvent"),
        ):
            if (item is None) != (not grams):
                raise ValidationError(
                    f"{code}: the {label} need both an item and a quantity."
                )
        if (self.liner_micron or self.liner_grams_per_bag) and self.liner_item_id is None:
            raise ValidationError(
                f"{code}: the sack has a liner and the specification does not say "
                "what it is, so it would weigh in every sack and cost in none."
            )


class BagCoatingLine(AuditModel):
    """
    One polymer in a sack's coating blend, as a share by weight.

    Written only through its specification (`set_coating()` and save),
    which checks the blend and rebuilds the bill with it: a line saved
    on its own would change what the sack is coated with and leave the
    bill saying otherwise.
    """

    specification = models.ForeignKey(
        BagSpecification, on_delete=models.CASCADE, related_name="coating_lines"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    parts = models.DecimalField(
        max_digits=10, decimal_places=3,
        help_text="Relative share by weight: 80 and 20, or 4 and 1.",
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["specification", "item"],
                                    name="one_coating_line_per_item"),
            models.CheckConstraint(check=Q(parts__gt=0), name="coating_parts_positive"),
        ]

    def _refuse_unless_through_specification(self):
        if not getattr(self, "_via_specification", False):
            raise ValidationError(
                "A coating line is changed through its specification: "
                "set_coating() and save it, so the blend is checked and the "
                "bill rebuilt with it."
            )

    def save(self, *args, **kwargs):
        self._refuse_unless_through_specification()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._refuse_unless_through_specification()
        return super().delete(*args, **kwargs)


def denier_for(gsm, ends_per_inch, picks_per_inch, shrink_percent, warp_tape_denier=None):
    """
    The deniers that weave `gsm` at a mesh: in the fabric, and on the
    tape line, which runs finer by the shrink.

    With no warp given, warp and weft are the same tape. With one given
    (as the tape line runs it), the weft is whatever makes up the rest.
    """
    gsm, ends, picks = Decimal(gsm), Decimal(ends_per_inch), Decimal(picks_per_inch)
    if ends <= 0 or picks <= 0:
        raise ValidationError("A mesh needs tapes both ways: ends and picks per inch.")
    keep = Decimal("1") - _percent(Decimal(shrink_percent))
    if not 0 < keep <= 1:
        raise ValidationError("Shrink is a percentage from 0 up to, not including, 100.")
    per_denier = INCHES_PER_METRE / DENIER_LENGTH_M
    if warp_tape_denier is None:
        warp = weft = gsm / ((ends + picks) * per_denier)
    else:
        warp = Decimal(warp_tape_denier) / keep
        weft = (gsm / per_denier - ends * warp) / picks
        if weft <= 0:
            raise ValidationError(
                f"A {warp_tape_denier} denier warp at {ends} ends already weighs "
                f"{gsm:.1f} GSM or more; there is nothing left for the weft."
            )
    return {
        "warp_fabric_denier": warp, "weft_fabric_denier": weft,
        "warp_tape_denier": warp * keep, "weft_tape_denier": weft * keep,
    }


def sack_units(item, root, on_date):
    """
    A sack's weight, for whoever counts it by weight: the specification in
    force on the day, at the weight contracted where there is one.

    Registered with inventory when the app loads. Derived each time it is
    asked, so a changed specification changes the answer; a document that
    converted keeps the figure it used, on its stock movement.
    """
    from apps.core.windows import covers

    specs = [
        spec for spec in BagSpecification.objects.filter(bag_item=item, is_active=True)
        .select_related("fabric__fabric_item__uom")
        if covers(spec.valid_from, spec.valid_to, on_date)
    ]
    if len(specs) != 1:
        return None
    spec = specs[0]
    kilogramme = spec.fabric.fabric_item.uom
    if kilogramme.root().pk != root.pk:
        return None
    grams = spec.target_grams if spec.target_grams is not None else spec.bag_grams()
    return kilogramme, GRAMMES_PER_KG / grams
