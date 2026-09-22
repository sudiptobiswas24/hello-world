"""
What it takes to make something, and what comes off the line with it.

This module knows nothing about woven sacks, or about any other
product. It holds quantities of items against an item, and it explodes
them. Everything that knows what a denier is lives in `woven.py`, which
builds these rows rather than replacing them — so the machinery that
issues material and books a variance never has to ask what industry it
is in.

Three decisions here are worth arguing with, because getting any of
them wrong is expensive and none of them is obvious:

**Waste is a fraction of the input, not of the output.** Feed a hundred
kilos of polymer into an extruder that loses three per cent and
ninety-seven kilos of tape comes out; to get a hundred kilos of tape
you must feed 100/0.97, not 100x1.03. The two agree to three decimal
places over one stage and diverge over four, which is exactly how many
stages a printed laminated sack passes through. At five hundred tonnes
a month the difference is not academic.

**A by-product is not a negative component.** Extrusion trim, loom
waste and cut-and-stitch offcuts are reground and fed back in, and they
are worth money — less than virgin polymer, more than nothing. Booking
them as negative consumption would net them against the input and
destroy the one number the plant actually manages: how much polymer
went in and how much came back. They are produced, valued, and
received into stock as their own item.

**The graph is cyclic, because the regrind loop is real.** Tape
consumes regrind and produces regrind. Anything walking this structure
without remembering where it has been will walk forever, so `explode()`
carries its path and refuses to re-enter an item it is already inside.
"""

from collections import namedtuple
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import AuditModel
from apps.inventory.models import Item

ONE_HUNDRED = Decimal("100")


class ByproductValuation(models.TextChoices):
    STANDARD = "standard", "At the item's standard cost"
    SHARE = "share", "At a share of what the run cost"
    NONE = "none", "At nothing"


class BillOfMaterials(AuditModel):
    """
    One way of making one item, written for a stated batch size.

    The batch matters. A sack BOM is written per thousand bags and a
    tape BOM per hundred kilos, because that is how the shop floor
    talks and because a per-unit figure rounded to four places and then
    multiplied by a hundred thousand is wrong by a visible amount. Every
    requirement is scaled from the batch, never accumulated a unit at a
    time.
    """

    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="boms",
        help_text="What this makes.",
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Which revision of this BOM it is. Several may exist for one "
                  "item; only one of them can be the default an unqualified "
                  "explosion picks.",
    )
    name = models.CharField(max_length=255, blank=True)
    quantity_produced = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="How much of the item one run of this BOM makes, in `uom`.",
    )
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+",
        help_text="The unit the batch size above is written in.",
    )
    is_computed = models.BooleanField(
        default=False, editable=False,
        help_text="Set when something else works this BOM's numbers out — a "
                  "product specification, in this plant's case. Such a BOM "
                  "refuses to be edited by hand: a hand edit would survive until "
                  "the next rebuild and no longer, which is worse than refusing.",
    )
    routing = models.ForeignKey(
        "Routing", null=True, blank=True, on_delete=models.PROTECT,
        related_name="boms",
        help_text="How the thing is made, as against what it is made of. Here "
                  "rather than on the work order because it is a property of "
                  "the product: every run of it passes the same machines.",
    )
    is_default = models.BooleanField(
        default=True,
        help_text="The one an explosion picks when it reaches this item and "
                  "nobody named a BOM. An item may have several ways of being "
                  "made — one loom width or another — but only one of them can "
                  "be the answer to an unqualified question.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["item__sku", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "version"], name="one_bom_per_item_and_version"
            ),
            # Two defaults is no default: an explosion would pick whichever
            # the database happened to return first, and the same order
            # costed twice would cost two different amounts.
            models.UniqueConstraint(
                fields=["item"], condition=Q(is_default=True, is_active=True),
                name="one_default_bom_per_item",
            ),
            models.CheckConstraint(
                check=Q(quantity_produced__gt=0), name="bom_batch_is_positive",
            ),
        ]

    def __str__(self):
        label = self.name or f"{self.item.sku} v{self.version}"
        return f"{label} ({self.quantity_produced} {self.uom})"

    def computed_by(self):
        """
        Whatever works this BOM out, or None.

        Found by reflection rather than by a field, because this module
        does not know what kinds of thing compute a BOM and should not
        have to be edited when a new one appears.
        """
        for relation in self._meta.related_objects:
            if not relation.one_to_one:
                continue
            owner = getattr(self, relation.get_accessor_name(), None)
            if owner is not None:
                return owner
        return None

    def scale_for(self, quantity, uom=None):
        """
        How many runs of this BOM make `quantity` of the item.

        Returned as a ratio rather than applied here, because every
        component and by-product is scaled by the same one and a ratio
        computed once cannot disagree with itself.
        """
        if uom is not None and uom.pk != self.uom_id:
            quantity = uom.convert_to(quantity, self.uom)
        return Decimal(quantity) / self.quantity_produced

    def save(self, *args, **kwargs):
        if self.is_computed and not getattr(self, "_rebuilding", False):
            raise ValidationError(
                f"{self} is computed from {self.computed_by() or 'a specification'}. "
                "Change that; this BOM is rebuilt from it."
            )
        super().save(*args, **kwargs)


class BomComponent(AuditModel):
    """What goes in, and how much of it never comes out again."""

    bom = models.ForeignKey(
        BillOfMaterials, on_delete=models.CASCADE, related_name="components"
    )
    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="component_of"
    )
    quantity = models.DecimalField(
        max_digits=18, decimal_places=6,
        help_text="Net requirement for one run of the BOM, before waste — what "
                  "ends up in the product. Six decimal places because a sack's "
                  "stitching thread is grammes per thousand bags and rounding it "
                  "to four loses the line.",
    )
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+",
        help_text="The unit the quantity above is written in.",
    )
    waste_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("0"),
        help_text="Percentage of what is FED IN that never reaches the product — "
                  "extruder purge, loom breaks, cut-and-stitch offcut. Gross "
                  "requirement is net / (1 - waste/100), not net x (1 + waste/100): "
                  "the loss is measured on the input, and over the four stages a "
                  "printed laminated sack passes through the two readings diverge.",
    )
    line_number = models.PositiveIntegerField(default=0)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["bom", "line_number", "id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="bom_component_quantity_positive",
            ),
            # At a hundred per cent every kilo fed in is lost, so no
            # quantity of input makes any output and the gross
            # requirement is a division by zero.
            models.CheckConstraint(
                check=Q(waste_percent__gte=0) & Q(waste_percent__lt=100),
                name="bom_component_waste_under_one_hundred",
            ),
        ]

    def __str__(self):
        return f"{self.quantity} {self.uom} {self.item.sku}"

    def gross_quantity(self):
        """
        What must be issued for one run, waste included.

        Net over one-minus-waste, for the reason the field's help text
        gives: the percentage is of the input.
        """
        if not self.waste_percent:
            return self.quantity
        return self.quantity / (Decimal("1") - self.waste_percent / ONE_HUNDRED)

    def save(self, *args, **kwargs):
        if self.bom.is_computed and not getattr(self, "_rebuilding", False):
            raise ValidationError(
                f"{self.bom} is computed from "
                f"{self.bom.computed_by() or 'a specification'}, and its "
                "components with it. Change that."
            )
        self.item.check_uom(self.uom)
        super().save(*args, **kwargs)


class BomByproduct(AuditModel):
    """
    What else comes off the line, and what it is worth.

    Regrind is the case this exists for. A tonne of polymer entering an
    extrusion plant leaves as tape, as reground trim, and as dust; the
    trim is worth perhaps two thirds of virgin granule and goes straight
    back into the hopper. Netting it off the polymer input would make
    the run look cheaper and would destroy the only reconciliation that
    matters here — what went in against what came back.
    """

    bom = models.ForeignKey(
        BillOfMaterials, on_delete=models.CASCADE, related_name="byproducts"
    )
    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="byproduct_of"
    )
    quantity = models.DecimalField(
        max_digits=18, decimal_places=6,
        help_text="Expected output per run of the BOM, in `uom`.",
    )
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+"
    )
    valuation = models.CharField(
        max_length=16, choices=ByproductValuation.choices,
        default=ByproductValuation.STANDARD,
        help_text="How much of the run's cost this carries out with it. Reground "
                  "trim is worth a standard recovery value, well under virgin "
                  "polymer; sweepings are worth nothing and should say so rather "
                  "than quietly absorbing a share.",
    )
    cost_share_percent = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True,
        help_text="Used only when valuation is 'share': the percentage of the "
                  "run's total cost this by-product carries.",
    )
    line_number = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["bom", "line_number", "id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="bom_byproduct_quantity_positive",
            ),
            # A share valuation with no share is a by-product that will
            # be valued at whatever None happens to mean downstream.
            models.CheckConstraint(
                check=~Q(valuation="share") | Q(cost_share_percent__isnull=False),
                name="bom_byproduct_share_needs_a_percentage",
            ),
        ]

    def __str__(self):
        return f"{self.quantity} {self.uom} {self.item.sku}"

    def save(self, *args, **kwargs):
        if self.bom.is_computed and not getattr(self, "_rebuilding", False):
            raise ValidationError(
                f"{self.bom} is computed from "
                f"{self.bom.computed_by() or 'a specification'}, and its "
                "by-products with it. Change that."
            )
        self.item.check_uom(self.uom)
        super().save(*args, **kwargs)


Requirement = namedtuple(
    "Requirement", "item quantity uom level bom is_byproduct is_leaf path"
)


def default_bom_for(item):
    """The BOM an explosion uses when it reaches this item unqualified."""
    return item.boms.filter(is_default=True, is_active=True).first()


def explode(bom, quantity, uom=None, _path=()):
    """
    Everything it takes to make `quantity` of what `bom` makes, all the
    way down to what is bought rather than made.

    Depth first, so the rows read in the order the plant works in:
    polymer before tape, tape before fabric, fabric before the sack. An
    item with no BOM of its own is a leaf — it is bought, and there is
    nothing below it.

    The path is carried because this graph has cycles in it. Tape
    consumes regrind and produces regrind, and fabric waste goes back
    the same way; a walk that does not remember where it has been will
    not come back. A component the walk is already inside is recorded as
    a leaf and not entered: it is a real requirement, taken from a real
    shelf, and it is not a sub-assembly.
    """
    scale = bom.scale_for(quantity, uom)
    level = len(_path)
    path = _path + (bom.item_id,)
    rows = []
    for component in bom.components.select_related("item", "uom").all():
        required = component.gross_quantity() * scale
        below = (
            None if component.item_id in path
            else default_bom_for(component.item)
        )
        rows.append(Requirement(
            item=component.item, quantity=required, uom=component.uom,
            level=level + 1, bom=bom, is_byproduct=False,
            is_leaf=below is None, path=path,
        ))
        if below is not None:
            rows.extend(explode(below, required, component.uom, path))
    for byproduct in bom.byproducts.select_related("item", "uom").all():
        rows.append(Requirement(
            item=byproduct.item, quantity=byproduct.quantity * scale,
            uom=byproduct.uom, level=level + 1, bom=bom,
            is_byproduct=True, is_leaf=True, path=path,
        ))
    return rows


def net_requirements(bom, quantity, uom=None):
    """
    The explosion flattened to what must be found on a shelf, by item.

    Only the leaves. An item this plant makes is not something to go
    looking for, it is a reason to raise another work order, and listing
    both it and the polymer it is made from would count the polymer
    twice. By-products are left out too: they are an output, and putting
    them on a shopping list asks a buyer to go and purchase the plant's
    own scrap.
    """
    totals = {}
    for row in explode(bom, quantity, uom):
        if row.is_byproduct or not row.is_leaf:
            continue
        key = (row.item.pk, row.uom.pk)
        entry = totals.setdefault(key, [row.item, Decimal("0"), row.uom])
        entry[1] += row.quantity
    return [tuple(entry) for entry in totals.values()]


def material_balance(bom, quantity, uom=None):
    """
    What a run needs against what it gives back, item by item.

    The number this exists for is regrind. A blend calling for fifteen
    per cent reprocessed material against a process that recovers three
    per cent of its throughput is a plant that must buy scrap from
    somebody, and nothing in a conventional bill of materials says so —
    the requirement and the recovery sit in different documents and
    never meet. Here they are the same row.

    Returned as [(item, required, produced, net)] with net positive when
    the run must find the material and negative when it makes more than
    it uses.
    """
    rows = {}
    for row in explode(bom, quantity, uom):
        entry = rows.setdefault(
            row.item.pk, [row.item, Decimal("0"), Decimal("0")]
        )
        if row.is_byproduct:
            entry[2] += row.quantity
        elif row.is_leaf:
            entry[1] += row.quantity
    return [
        (item, required, produced, required - produced)
        for item, required, produced in rows.values()
    ]


PlannedCost = namedtuple(
    "PlannedCost", "materials conversion byproducts net"
)


def planned_cost(bom, quantity, warehouse, uom=None):
    """
    What a run of this BOM is expected to cost, from today's shelf.

    Materials at what taking them off the shelf would take off its
    value, less what the by-products carry out with them. One level
    only: a sub-assembly this plant makes is valued at what it is worth
    in stock, not re-exploded, because that is what will actually be
    issued to the run.

    Expected, and no more than that. It is frozen onto a work order at
    release and the run is received at it, because the real cost of a
    run is not known until it closes and a lorry will not wait. What
    the run actually consumed lands as a variance when it does close.

    Materials, machine time and the by-product credit are returned apart
    as well as netted: a by-product taking a share of the run has to
    take it of something, and at close the material overrun and the time
    overrun have to be told apart.

    Machine time is what the routing says it is, at the work centres'
    own rates. A plant with no routing, or one that leaves its rates at
    zero, gets nothing here — and its finished goods are then worth
    their materials, which is about three quarters of what they cost.
    """
    from apps.inventory.costing import unit_cost_for

    scale = bom.scale_for(quantity, uom)
    batch_quantity = quantity
    if uom is not None and uom.pk != bom.uom_id:
        batch_quantity = uom.convert_to(quantity, bom.uom)
    materials = Decimal("0")
    for component in bom.components.select_related("item", "uom").all():
        required = component.gross_quantity() * scale
        in_stock_units = component.item.to_stock_quantity(required, component.uom)
        rate = unit_cost_for(component.item, warehouse, in_stock_units)
        if not rate:
            # Nothing of it on the shelf to take a price from. A standard
            # answers that; silence prices the material at nothing and
            # buries its whole cost in the variance at close, which is a
            # plan that was never a plan.
            if component.item.standard_cost is None:
                raise ValidationError(
                    f"{component.item} has none of itself at {warehouse} to take "
                    "a cost from and no standard cost, so a run using it would "
                    "be planned as though it were free. Set a standard cost."
                )
            rate = component.item.standard_cost
        materials += rate * in_stock_units
    conversion = Decimal("0")
    if bom.routing_id is not None:
        for operation in bom.routing.operations.select_related("work_centre"):
            minutes = operation.minutes_for(batch_quantity, bom.uom)
            conversion += (
                minutes / Decimal("60")
                * operation.work_centre.conversion_rate_per_hour()
            )
    credit = Decimal("0")
    for byproduct in bom.byproducts.select_related("item", "uom").all():
        credit += byproduct_value(
            byproduct, byproduct.quantity * scale, materials + conversion
        )
    return PlannedCost(
        materials, conversion, credit, materials + conversion - credit
    )


def byproduct_value(byproduct, quantity, run_cost=None):
    """
    What a by-product carries out of the run with it.

    Reground trim is worth a standard recovery value, well under virgin
    polymer. Sweepings are worth nothing and say so, rather than
    silently absorbing a share of the run and making the sack look
    cheaper than it was.
    """
    if byproduct.valuation == ByproductValuation.NONE:
        return Decimal("0")
    if byproduct.valuation == ByproductValuation.SHARE:
        if run_cost is None:
            return Decimal("0")
        return run_cost * (byproduct.cost_share_percent or Decimal("0")) / ONE_HUNDRED
    standard = byproduct.item.standard_cost
    if standard is None:
        raise ValidationError(
            f"{byproduct.item} comes off this run as a by-product valued at its "
            "standard cost, and it has no standard cost. Set one, or say the "
            "by-product is worth nothing — leaving it unanswered values the "
            "whole run against the main product without saying so."
        )
    quantity = byproduct.item.to_stock_quantity(quantity, byproduct.uom)
    return standard * quantity
