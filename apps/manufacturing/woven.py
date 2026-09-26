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
from django.db.models import Q

from apps.core.models import AuditModel, UnitOfMeasureCategory
from apps.inventory.models import Item

from apps.quality.models import (
    Characteristic,
    CharacteristicKind,
    Evaluation,
    InspectionPlan,
    PlanLine,
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

# The batch each generated BOM is written for. A thousand sacks makes
# grammes per bag and kilos per batch the same number; a hundred kilos
# of tape or fabric makes a blend percentage and a component quantity
# the same number. Both save a rounding step that would otherwise be
# taken a hundred thousand times.
TAPE_BATCH_KG = Decimal("100")
FABRIC_BATCH_KG = Decimal("100")
BAG_BATCH_PIECES = Decimal("1000")


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
        plan.is_mandatory = item.tracking != "none"
        plan._rebuilding = True
        plan.save()
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
            bom = BillOfMaterials(
                item=self.bom_item(), name=str(self),
                quantity_produced=self.bom_batch(), uom=self.bom_uom(),
                is_computed=True,
            )
        else:
            bom.item = self.bom_item()
            bom.name = str(self)
            bom.quantity_produced = self.bom_batch()
            bom.uom = self.bom_uom()
        bom.routing = self.bom_routing()
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


class TapeSpecification(SpecificationMixin, AuditModel):
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
        return [(
            "DENIER", "Denier", ("den", "Denier"), self.denier,
            self.denier - margin, self.denier + margin, 3,
            Evaluation.MEAN, "denier",
        )]

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
        self._check_units()
        super().save(*args, **kwargs)
        self.rebuild_bom()
        self.rebuild_inspection_plan()


class FabricSpecification(SpecificationMixin, AuditModel):
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
    weave = models.CharField(
        max_length=8, choices=Weave.choices, default=Weave.TUBULAR
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
        ]

    def __str__(self):
        return f"{self.code} ({self.gsm():.1f} GSM, {self.lay_flat_width_cm} cm)"

    # -- the arithmetic -------------------------------------------------

    def weft(self):
        """The weft tape, which is the warp tape unless it was named."""
        return self.weft_tape or self.warp_tape

    def warp_grams_per_sqm(self):
        return (
            self.ends_per_inch * INCHES_PER_METRE
            * self.warp_tape.denier / DENIER_LENGTH_M
        )

    def weft_grams_per_sqm(self):
        return (
            self.picks_per_inch * INCHES_PER_METRE
            * self.weft().denier / DENIER_LENGTH_M
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
                f"of {self.warp_tape.denier} denier tape makes {self.gsm():.1f} "
                f"GSM, which is {deviation:.1f}% from the {self.target_gsm} GSM "
                f"quoted and outside the {self.gsm_tolerance_percent}% tolerance. "
                "Change the mesh, the denier or the quotation — the loom will not "
                "split the difference."
            )
        super().save(*args, **kwargs)
        self.rebuild_bom()
        self.rebuild_inspection_plan()


class BagSpecification(SpecificationMixin, AuditModel):
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
                  "width: the tube is woven at the width of the bag it becomes.",
    )
    bag_length_cm = models.DecimalField(
        max_digits=8, decimal_places=2,
        help_text="The finished sack's length, hems excluded.",
    )
    bottom_hem_cm = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("3"),
        help_text="Fabric turned up and stitched at the bottom. Real fabric, "
                  "cut and paid for, and left out of more than one costing.",
    )
    top_hem_cm = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("2"),
        help_text="Fabric folded at the mouth, hemmed or left raw.",
    )
    is_laminated = models.BooleanField(default=False)
    lamination_gsm = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="Weight of the coating per square metre of fabric, typically "
                  "12 to 20. Applied over the same area the fabric covers.",
    )
    lamination_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="The polymer the coating is extruded from.",
    )
    lamination_waste_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("4"),
        help_text="Of the coating polymer fed in. A coating line's own loss, "
                  "separate from what cutting and stitching waste.",
    )
    print_colours = models.PositiveSmallIntegerField(default=0)
    printed_faces = models.PositiveSmallIntegerField(
        default=2,
        help_text="How many faces of the sack carry the print. Two is the usual "
                  "answer and it is twice the ink of one.",
    )
    ink_grams_per_sqm_per_colour = models.DecimalField(
        max_digits=8, decimal_places=3, default=Decimal("3"),
        help_text="Ink laid down per square metre per colour. Small, and the "
                  "only reason it is here is that a plant printing four colours "
                  "on a million sacks is buying ink by the tonne.",
    )
    ink_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    thread_grams_per_bag = models.DecimalField(
        max_digits=8, decimal_places=3, default=Decimal("1.2"),
        help_text="Sewing thread in the bottom seam.",
    )
    thread_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    liner_item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="An inner LDPE liner, for sacks that must keep moisture out.",
    )
    liner_grams_per_bag = models.DecimalField(
        max_digits=8, decimal_places=3, default=Decimal("0")
    )
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
    weight_tolerance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("5"),
        help_text="How far a finished sack may be from its computed weight. "
                  "The number a customer's own goods-in scale argues about.",
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
        cm carries 2 x 0.60 x 1.05 square metres — not 0.63.
        """
        return (
            self.fabric.layers()
            * (self.bag_width_cm / CM_PER_M)
            * (self.cut_length_cm() / CM_PER_M)
        )

    def printed_area_sqm(self):
        return (
            Decimal(self.printed_faces)
            * (self.bag_width_cm / CM_PER_M)
            * (self.bag_length_cm / CM_PER_M)
        )

    def fabric_grams(self):
        return self.fabric_area_sqm() * self.fabric.gsm()

    def lamination_grams(self):
        if not self.is_laminated:
            return Decimal("0")
        return self.fabric_area_sqm() * self.lamination_gsm

    def ink_grams(self):
        if not self.print_colours:
            return Decimal("0")
        return (
            self.printed_area_sqm()
            * self.ink_grams_per_sqm_per_colour
            * Decimal(self.print_colours)
        )

    def bag_grams(self):
        """
        What one finished sack weighs.

        The number a customer checks on a weighbridge and the number a
        costing is built on, and they had better be the same one.
        """
        return (
            self.fabric_grams() + self.lamination_grams() + self.ink_grams()
            + self.thread_grams_per_bag + self.liner_grams_per_bag
        )

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
        if self.is_laminated and self.lamination_item is not None:
            rows.append((
                self.lamination_item, self.lamination_grams(),
                self.lamination_item.uom, self.lamination_waste_percent,
                f"Coating at {self.lamination_gsm} GSM",
            ))
        if self.print_colours and self.ink_item is not None:
            rows.append((
                self.ink_item, self.ink_grams(), self.ink_item.uom,
                self.conversion_waste_percent,
                f"Ink, {self.print_colours} colour(s) on {self.printed_faces} face(s)",
            ))
        if self.thread_item is not None:
            rows.append((
                self.thread_item, self.thread_grams_per_bag,
                self.thread_item.uom, self.conversion_waste_percent,
                "Sewing thread",
            ))
        if self.liner_item is not None and self.liner_grams_per_bag:
            rows.append((
                self.liner_item, self.liner_grams_per_bag,
                self.liner_item.uom, self.conversion_waste_percent, "Inner liner",
            ))
        return rows

    def inspection_lines(self):
        weight = self.bag_grams()
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
        )
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
            (self.lamination_item, "the coating polymer"),
            (self.ink_item, "the ink"),
            (self.thread_item, "the thread"),
            (self.liner_item, "the liner"),
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
        if self.is_laminated and self.lamination_item is None:
            raise ValidationError(
                f"{self.code}: the sack is laminated and the specification does "
                "not say what with."
            )
        if self.print_colours and self.ink_item is None:
            raise ValidationError(
                f"{self.code}: the sack is printed in {self.print_colours} "
                "colour(s) and the specification does not say what with."
            )
        super().save(*args, **kwargs)
        self.rebuild_bom()
        self.rebuild_inspection_plan()
