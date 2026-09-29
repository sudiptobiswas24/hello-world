"""
Quoting a sack from its specification: what it costs at today's
market, and what it is offered at.

The plant costs a quotation the way its own calculator did — polymer
at this week's rate, each stage at so much a kilogram, overhead and
margin on top — and this does the same, with three things the
calculator did not:

**Dated rates, never edited.** A rate is what was decided from its
date. Polymer moving from 102 to 104 is a new row from Monday, so the
history is the rows, and a quotation costed last week can be read back
at last week's figures. Only a rate not yet in force may be deleted:
that is a typing mistake, not history.

**Each stage charged on what it makes.** Tape extrusion on the tape,
weaving on the fabric, lamination, printing and cutting on the roll
(fabric, coating and film). The calculator charged every stage on the
whole sack, so the tape line was paid for the liner and the handle.

**A cost sheet is frozen.** Every quantity, rate, share and total is
written down when it is costed. A rate that changes tomorrow moves the
next sheet, not one a customer has already been sent.

**Conversion from one source.** A stage's rate may be typed, or it may
name the work centre that does the stage. Named, the rate is not a
second opinion: it is the work centre's hourly conversion rate — the
machine, labour and overhead a time booking absorbs into the run —
over what that centre achieves an hour on the routing of the bill being
costed, at the speed the specification itself sets. A quote and the
run it becomes then charge the same hour the same, and a re-rated line
or a faster take-up moves both. Run time only: setup is a cost of the
order, not of the kilogramme, and is left to overhead.

The walk is the bill of materials, level by level, as the standard
cost roll-up walks it: the sack's own bill, its fabric's, the tapes'.
Anything outside that chain is priced from the rate sheet, which is
also how a loop is broken — regrind goes into the tape and comes off
every stage, and is worth what the plant says it is worth.
"""

import datetime
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel

ZERO = Decimal("0")
PLACES = Decimal("0.000001")
PAISA = Decimal("0.01")
HUNDRED = Decimal("100")
GRAMMES_PER_KG = Decimal("1000")

PER_KG_STAGES = [
    ("tape", "Tape extrusion"), ("weaving", "Weaving"), ("lamination", "Lamination"),
    ("printing", "Printing"), ("cutting", "Cutting and stitching"),
]
PER_BAG_STAGES = [
    ("valve", "Valve fixing"), ("dcut", "D-cut punching"), ("handle", "Handle attachment"),
    ("liner", "Liner insertion"), ("packing", "Bale packing"),
]
STAGES = PER_KG_STAGES + PER_BAG_STAGES
STAGE_NAMES = dict(STAGES)


class DatedRate(AuditModel):
    """A figure in force from a date: added, superseded, never rewritten."""

    valid_from = models.DateField()
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(
                "A rate is what was decided from its date, and quotations were "
                "costed at it. Enter a new one from the date it changes."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.valid_from <= timezone.localdate():
            raise ValidationError(
                f"This has been in force since {self.valid_from}. Supersede it "
                "with a new one; only a rate not yet in force can be deleted."
            )
        return super().delete(*args, **kwargs)

    @classmethod
    def in_force(cls, on_date, **match):
        return cls.objects.filter(valid_from__lte=on_date, **match).order_by("-valid_from").first()


class MaterialRate(DatedRate):
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="material_rates")
    rate = models.DecimalField(
        max_digits=14, decimal_places=4,
        help_text="Per unit the item is stocked in — per kilogramme for polymer.",
    )

    class Meta:
        ordering = ["item__sku", "-valid_from"]
        constraints = [
            models.UniqueConstraint(fields=["item", "valid_from"], name="one_material_rate_a_day"),
            models.CheckConstraint(check=Q(rate__gte=0), name="material_rate_not_negative"),
        ]

    def __str__(self):
        return f"{self.item.sku} at {self.rate} from {self.valid_from}"


class StageRate(DatedRate):
    stage = models.CharField(max_length=16, choices=STAGES)
    rate = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text="Per kilogramme the stage makes, or per sack for the per-sack "
                  "operations (valve, D-cut, handle, liner, packing). Blank when "
                  "the rate comes from the work centre.",
    )
    work_centre = models.ForeignKey(
        "manufacturing.WorkCentre", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="The centre that does this stage. Named, the rate is its hourly "
                  "conversion rate over what it achieves an hour on the specification "
                  "being costed — the same hour a run is charged.",
    )

    class Meta:
        ordering = ["stage", "-valid_from"]
        constraints = [
            models.UniqueConstraint(fields=["stage", "valid_from"], name="one_stage_rate_a_day"),
            models.CheckConstraint(check=Q(rate__isnull=True) | Q(rate__gte=0),
                                   name="stage_rate_not_negative"),
            models.CheckConstraint(check=Q(stage__in=[code for code, _ in STAGES]),
                                   name="stage_rate_stage_known"),
            models.CheckConstraint(
                check=(Q(rate__isnull=False) & Q(work_centre__isnull=True))
                | (Q(rate__isnull=True) & Q(work_centre__isnull=False)),
                name="stage_rate_typed_or_from_a_work_centre"),
        ]

    def __str__(self):
        figure = self.rate if self.work_centre_id is None else f"{self.work_centre.code}'s rate"
        return f"{STAGE_NAMES.get(self.stage, self.stage)} at {figure} from {self.valid_from}"

    def per_unit(self, bom, stage_uom, per_bill_unit, on_date):
        """
        What one `stage_uom` of this stage costs, made on `bom`.

        Typed, the rate. From a work centre: its hourly conversion rate
        over what it achieves an hour, for every operation of the bill's
        routing at that centre (two passes cost two). An operation rated
        in the stage's own unit is read in it; one rated in what the
        bill makes — sacks — is read per sack and spread over the
        `per_bill_unit` of stage a sack takes. Refused, with the reason,
        where the routing does not pass the centre or reads in neither.
        """
        if self.work_centre_id is None:
            return self.rate
        centre = self.work_centre
        name = STAGE_NAMES[self.stage].lower()
        if (centre.updated_at - centre.created_at > EDITED_AFTER
                and timezone.localtime(centre.updated_at).date() > on_date):
            raise ValidationError(
                f"{centre.code} was changed after {on_date} and its rates then are not "
                f"kept, so {name} cannot be costed from it on that day")
        steps = [] if bom.routing_id is None else list(
            bom.routing.operations.filter(work_centre=centre))
        if not steps:
            raise ValidationError(f"{bom} does not pass {centre.code}, which {name} is "
                                  "costed from")
        hourly = centre.conversion_rate_per_hour()
        total = ZERO
        for step in steps:
            try:
                total += hourly / step.rate(stage_uom, bom=bom)
            except ValidationError:
                try:
                    total += hourly / step.rate(bom.uom, bom=bom) / per_bill_unit
                except ValidationError as error:
                    raise ValidationError(f"{name} cannot be costed from {centre.code}: "
                                          f"{' '.join(error.messages)}")
        return total


class QuotePolicy(DatedRate):
    overhead_percent = models.DecimalField(
        max_digits=6, decimal_places=2,
        help_text="On material and conversion together.",
    )
    margin_percent = models.DecimalField(
        max_digits=6, decimal_places=2,
        help_text="On cost, unless a cost sheet is given its own.",
    )

    class Meta:
        ordering = ["-valid_from"]
        constraints = [
            models.UniqueConstraint(fields=["valid_from"], name="one_quote_policy_a_day"),
            models.CheckConstraint(check=Q(overhead_percent__gte=0), name="overhead_not_negative"),
        ]

    def __str__(self):
        return f"Overhead {self.overhead_percent}%, margin {self.margin_percent}% from {self.valid_from}"


class LineKind(models.TextChoices):
    MATERIAL = "material", "Material"
    CONVERSION = "conversion", "Conversion"
    CREDIT = "credit", "By-product credit"


class CostSheet(AuditModel):
    """One sack costed on one day, and everything that went into the number."""

    specification = models.ForeignKey(
        "manufacturing.BagSpecification", on_delete=models.PROTECT, related_name="cost_sheets"
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=2,
                                   help_text="Sacks in the enquiry.")
    costed_on = models.DateField()
    bag_grams = models.DecimalField(max_digits=12, decimal_places=4)
    overhead_percent = models.DecimalField(max_digits=6, decimal_places=2)
    margin_percent = models.DecimalField(max_digits=6, decimal_places=2)
    material = models.DecimalField(max_digits=16, decimal_places=6)
    conversion = models.DecimalField(max_digits=16, decimal_places=6)
    credit = models.DecimalField(max_digits=16, decimal_places=6)
    overhead = models.DecimalField(max_digits=16, decimal_places=6)
    cost = models.DecimalField(max_digits=16, decimal_places=6,
                               help_text="Per sack, overhead included.")
    price = models.DecimalField(max_digits=16, decimal_places=6,
                                help_text="Per sack, margin included, before rounding.")
    quoted_price = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="What goes on the quotation: the price to the paisa.",
    )
    quotation_line = models.OneToOneField(
        "sales.QuotationLine", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="cost_sheet", editable=False,
    )

    class Meta:
        ordering = ["-costed_on", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="cost_sheet_quantity_positive"),
        ]

    def __str__(self):
        return f"{self.specification.code} on {self.costed_on}: {self.quoted_price} a sack"

    def save(self, *args, **kwargs):
        if not self._state.adding and not getattr(self, "_quoting", False):
            raise ValidationError(
                "A cost sheet is what the sack cost on the day it was costed. "
                "Cost it again rather than change it."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.quotation_line_id is not None:
            raise ValidationError(
                "This cost sheet priced a quotation line, and is the record of how "
                "that price was reached. Remove the line first."
            )
        return super().delete(*args, **kwargs)

    def per_kg(self):
        return (self.cost / self.bag_grams * GRAMMES_PER_KG).quantize(PLACES)

    def order_value(self):
        return (self.quoted_price * self.quantity).quantize(PAISA)


class CostSheetLine(models.Model):
    sheet = models.ForeignKey(CostSheet, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=16, choices=LineKind.choices)
    stage = models.CharField(max_length=16, choices=STAGES, blank=True)
    item = models.ForeignKey("inventory.Item", null=True, blank=True,
                             on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(
        max_digits=16, decimal_places=6,
        help_text="Per sack: kilogrammes of material, of by-product, or made "
                  "by the stage; one for a per-sack operation.",
    )
    rate = models.DecimalField(max_digits=14, decimal_places=4)
    amount = models.DecimalField(max_digits=16, decimal_places=6, help_text="Per sack.")
    last_receipt_cost = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="What the item last came in at, beside the rate it was costed at.",
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            # A credit is a positive amount the sheet subtracts, so nothing
            # on a line is ever below nought.
            models.CheckConstraint(
                check=Q(quantity__gte=0) & Q(rate__gte=0) & Q(amount__gte=0),
                name="cost_sheet_line_not_negative",
            ),
        ]

    def save(self, *args, **kwargs):
        if not getattr(self, "_writing", False):
            raise ValidationError("A cost sheet's lines are written when it is costed, and only then.")
        super().save(*args, **kwargs)


# -- the costing ---------------------------------------------------------

def _last_receipt_cost(item):
    from apps.inventory.models import MovementType, StockMovement

    row = (StockMovement.objects
           .filter(item=item, movement_type=MovementType.RECEIPT, unit_cost__isnull=False)
           .order_by("-occurred_at", "-id").first())
    return row.unit_cost if row else None


class _Walk:
    """The sack's bill, level by level, with the chain it was specified on."""

    def __init__(self, specification, on_date):
        self.on_date = on_date
        fabric = specification.fabric
        tapes = {fabric.warp_tape_id: fabric.warp_tape, fabric.weft().pk: fabric.weft()}
        # The chain as specified, not whatever bill is the default for
        # those items: a sack on this fabric is costed on this fabric.
        self.chain = {fabric.fabric_item_id: (fabric.bom, "weaving")}
        for tape in tapes.values():
            self.chain[tape.tape_item_id] = (tape.bom, "tape")
        self.material = defaultdict(lambda: ZERO)
        self.credit = defaultdict(lambda: ZERO)
        self.stage_kg = defaultdict(lambda: ZERO)
        # Which bill made each stage's kilogrammes: warp and weft may be
        # different tapes on different lines at different speeds.
        self.stage_parts = defaultdict(lambda: defaultdict(lambda: ZERO))
        self.problems = []

    def walk(self, bom, output, path=()):
        factor = output / bom.quantity_produced
        for component in bom.components.select_related("item__uom", "uom"):
            item = component.item
            quantity = item.to_stock_quantity(component.gross_quantity() * factor, component.uom)
            link = self.chain.get(item.pk)
            if link is not None and item.pk not in path:
                sub, stage = link
                self.stage_kg[stage] += quantity
                self.stage_parts[stage][(sub, item.uom)] += quantity
                self.walk(sub, item.uom.convert_to(quantity, sub.uom), path + (item.pk,))
            else:
                self.material[item] += quantity
        for byproduct in bom.byproducts.select_related("item__uom", "uom"):
            item = byproduct.item
            self.credit[item] += item.to_stock_quantity(byproduct.quantity * factor, byproduct.uom)


# Saving stamps created_at and updated_at a moment apart; a real edit
# is a later save.
EDITED_AFTER = datetime.timedelta(seconds=1)


def _recipe_changed_after(specification, on_date):
    """
    The part of the sack's chain edited after `on_date`, if any.

    Bills are rebuilt in place, so the recipe on an earlier day is not
    recorded. A chain created since is costed as defined; one edited
    since would be costed on a recipe it did not have then.
    """
    fabric = specification.fabric
    chain = [specification, fabric, fabric.warp_tape, fabric.weft()]
    for part in chain:
        edited = part.updated_at - part.created_at > EDITED_AFTER
        if edited and timezone.localtime(part.updated_at).date() > on_date:
            return part
    return None


def _rate(model, on_date, problems, label, **match):
    row = model.in_force(on_date, **match)
    if row is None:
        problems.append(label)
        return None
    return row.rate


def compute(specification, on_date):
    """
    Everything a cost sheet says, worked out and not yet written: the
    lines and the per-sack totals. Refuses naming every rate it lacks,
    not the first, so one round of data entry is enough.
    """
    from .bom import default_bom_for

    if not specification.is_active:
        raise ValidationError(f"{specification.code} is not active.")
    if specification.valid_to is not None and specification.valid_to < on_date:
        raise ValidationError(
            f"{specification.code} ended on {specification.valid_to}; a sack cannot "
            f"be costed on {on_date} to a specification no longer in force."
        )
    changed = _recipe_changed_after(specification, on_date)
    if changed is not None:
        raise ValidationError(
            f"{changed} was changed on {timezone.localtime(changed.updated_at):%Y-%m-%d}, "
            f"after {on_date}. The recipe as it stood then is not kept, so the sack "
            "cannot be costed on that day; cost it on or after the change."
        )
    policy = QuotePolicy.in_force(on_date)
    walk = _Walk(specification, on_date)
    walk.walk(specification.bom, Decimal("1"))

    problems = []
    if policy is None:
        problems.append(f"no overhead and margin in force on {on_date}")
    lines = []
    for kind, amounts in ((LineKind.MATERIAL, walk.material), (LineKind.CREDIT, walk.credit)):
        for item, quantity in sorted(amounts.items(), key=lambda pair: pair[0].sku):
            rate = MaterialRate.in_force(on_date, item=item)
            if rate is None:
                if default_bom_for(item, on_date) is not None:
                    problems.append(f"{item.sku} is made here outside this sack's chain; "
                                    "give it a rate to cost it at")
                else:
                    problems.append(f"no rate for {item.sku}")
                continue
            lines.append({
                "kind": kind, "stage": "", "item": item, "description": item.name,
                "quantity": quantity, "rate": rate.rate,
                "amount": quantity * rate.rate,
                "last_receipt_cost": _last_receipt_cost(item) if kind == LineKind.MATERIAL else None,
            })

    roll_items = {specification.fabric.fabric_item_id}
    roll_items |= {item.pk for item, _ in specification.coating_blend()}
    roll_items |= {specification.bopp_film_item_id, specification.metallic_film_item_id} - {None}
    per_sack = Decimal("1") / specification.bom.quantity_produced
    roll_kg = sum(
        (component.item.to_stock_quantity(component.gross_quantity() * per_sack, component.uom)
         for component in specification.bom.components.select_related("item__uom", "uom")
         if component.item_id in roll_items),
        ZERO,
    )
    # Each stage as (bill, unit, quantity a sack takes, stage per bill unit).
    bag = specification.bom
    kg = specification.fabric.fabric_item.uom
    stages = [(stage, [(sub, uom, quantity, Decimal("1"))
                       for (sub, uom), quantity in walk.stage_parts[stage].items()])
              for stage in ("tape", "weaving")]
    on_the_roll = [(bag, kg, roll_kg, roll_kg)]
    if specification.is_laminated:
        stages.append(("lamination", on_the_roll))
    if specification.print_colours or specification.print_colours_back:
        stages.append(("printing", on_the_roll))
    stages.append(("cutting", on_the_roll))
    for stage, present in (
        ("valve", specification.valve_patch_item_id), ("dcut", specification.dcut_area_sqcm),
        ("handle", specification.handle_item_id), ("liner", specification.liner_item_id),
        ("packing", True),
    ):
        if present:
            stages.append((stage, [(bag, bag.uom, Decimal("1"), Decimal("1"))]))
    for stage, parts in stages:
        rate = StageRate.in_force(on_date, stage=stage)
        if rate is None:
            problems.append(f"no rate for {STAGE_NAMES[stage].lower()}")
            continue
        try:
            amount = sum((quantity * rate.per_unit(bill, uom, per_bill_unit, on_date)
                          for bill, uom, quantity, per_bill_unit in parts), ZERO)
        except ValidationError as error:
            problems.append(" ".join(error.messages))
            continue
        quantity = sum((part[2] for part in parts), ZERO)
        lines.append({
            "kind": LineKind.CONVERSION, "stage": stage, "item": None,
            "description": STAGE_NAMES[stage], "quantity": quantity,
            # Typed, the rate as typed; derived, what the amount works out
            # at a unit — warp and weft at their own speeds.
            "rate": rate.rate if rate.work_centre_id is None
            else (amount / quantity).quantize(Decimal("0.0001")) if quantity else ZERO,
            "amount": amount, "last_receipt_cost": None,
        })
    if problems:
        raise ValidationError(
            f"{specification.code} cannot be costed on {on_date}: " + "; ".join(problems) + "."
        )

    def total(kind):
        return sum((line["amount"] for line in lines if line["kind"] == kind), ZERO)

    material, conversion, credit = (total(kind) for kind in
                                    (LineKind.MATERIAL, LineKind.CONVERSION, LineKind.CREDIT))
    subtotal = material + conversion - credit
    overhead = subtotal * policy.overhead_percent / HUNDRED
    return {
        "lines": lines, "policy": policy, "material": material, "conversion": conversion,
        "credit": credit, "overhead": overhead, "cost": subtotal + overhead,
    }


@transaction.atomic
def cost(specification, quantity, on_date=None, margin_percent=None):
    """Cost the sack and write the sheet down, frozen."""
    on_date = on_date or timezone.localdate()
    worked = compute(specification, on_date)
    margin = worked["policy"].margin_percent if margin_percent is None else Decimal(margin_percent)
    price = worked["cost"] * (1 + margin / HUNDRED)
    sheet = CostSheet(
        specification=specification, quantity=quantity, costed_on=on_date,
        bag_grams=specification.bag_grams().quantize(Decimal("0.0001")),
        overhead_percent=worked["policy"].overhead_percent, margin_percent=margin,
        material=worked["material"].quantize(PLACES),
        conversion=worked["conversion"].quantize(PLACES),
        credit=worked["credit"].quantize(PLACES),
        overhead=worked["overhead"].quantize(PLACES),
        cost=worked["cost"].quantize(PLACES), price=price.quantize(PLACES),
        quoted_price=price.quantize(PAISA, rounding=ROUND_HALF_UP),
    )
    sheet.full_clean(exclude=["quotation_line"])
    sheet.save()
    for row in worked["lines"]:
        line = CostSheetLine(
            sheet=sheet, kind=row["kind"], stage=row["stage"], item=row["item"],
            description=row["description"][:255], quantity=row["quantity"].quantize(PLACES),
            rate=row["rate"], amount=row["amount"].quantize(PLACES),
            last_receipt_cost=row["last_receipt_cost"],
        )
        line._writing = True
        line.save()
    return sheet


@transaction.atomic
def quote(sheet, quotation, taxes):
    """
    Put the costed price on a draft quotation, once. The taxes are the
    caller's to name — a quotation line carries its own, and quoting a
    sack tax-free because nobody said otherwise is the silent default
    this refuses.
    """
    from apps.sales.models import QuotationLine, QuotationStatus

    sheet = CostSheet.objects.select_for_update().get(pk=sheet.pk)
    if sheet.quotation_line_id is not None:
        raise ValidationError(
            f"This cost sheet is already quoted on {sheet.quotation_line.quotation}. "
            "Cost the sack again for another quotation."
        )
    if quotation.status != QuotationStatus.DRAFT:
        raise ValidationError(
            f"{quotation} is {quotation.get_status_display().lower()}; a price is added "
            "to a draft, and a sent quotation is revised instead."
        )
    if quotation.currency_id is not None and not quotation.currency.is_base:
        # Costed in the company's own currency from its own rates; put on
        # a quotation in another it would read rupees as dollars.
        raise ValidationError(
            f"{quotation} is in {quotation.currency}, and the sack was costed in the "
            "base currency. Quote it in the base currency, or convert the price "
            "yourself at a rate you choose."
        )
    spec = sheet.specification
    line = QuotationLine(
        quotation=quotation, item=spec.bag_item, uom=spec.bag_item.uom,
        quantity=sheet.quantity, unit_price=sheet.quoted_price,
        description=f"{spec.code}: {spec.construction()}, {sheet.bag_grams:.1f} g"[:255],
    )
    line.save()
    line.taxes.set(taxes)
    sheet.quotation_line = line
    sheet._quoting = True
    sheet.save(update_fields=["quotation_line", "updated_at"])
    return line
