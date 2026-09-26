"""
What a sack costs to make, rolled up level by level — and what
happens to the books when that number changes.

`planned_cost()` already answers "what will this run cost from
today's shelf", one level down, for a work order about to be
released. This answers a different question: what *should* a sack
cost, all the way down to the polymer, at a stated set of prices. A
plant needs both. The first prices a run; the second prices a
quotation, sets the standard that variances are measured against, and
is the only way to see that a sack's cost is four per cent filler and
sixty per cent polymer.

**A version, not a number.** Costs are rolled into a named set, so
next year's prices can be costed without disturbing this year's and
two people can argue about a quotation from the same figures.

**Rolled up by element, and kept apart.** Material, conversion and the
by-product credit are carried separately at every level and each
level's total becomes the next level's material. A sack whose cost
doubled is a different problem depending on which of the three moved,
and a single figure cannot say.

**Publishing a standard cost is a revaluation, and it posts.** This is
the part that matters and the hole this module was written to close.
Stock valued at standard is worth `quantity x standard_cost` — that is
what the method means — so changing the standard changed the value of
every shelf in the company instantly, silently, and with no journal
entry against it. The inventory account said one thing and the stock
valuation said another, for ever, and nothing in the system
disagreed. Publishing now posts the difference, or refuses.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import round_money
from apps.core.models import AuditModel, to_date

ZERO = Decimal("0")
RATE = Decimal("0.000001")
MINUTES_PER_HOUR = Decimal("60")

# A bill of materials deeper than this is a graph that has gone wrong
# rather than a product that is complicated. Printed laminated sacks
# run to five.
MAX_DEPTH = 40


class CostVersion(AuditModel):
    """
    One named set of standard costs.

    Named and dated rather than a single live number, because a plant
    costs next year's prices in November and does not want this
    November's quotations moving while it does.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    effective_from = models.DateField(
        help_text="The date this set of prices is meant to apply from. What "
                  "makes it real is publishing it, not this date.",
    )
    notes = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True, editable=False)
    published_on = models.DateField(
        null=True, blank=True, editable=False,
        help_text="The date the revaluation was posted on.",
    )
    published_by = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    revaluation_entry = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+", editable=False,
        help_text="What publishing this did to the books. A standard cost "
                  "change is a revaluation, and a revaluation posts.",
    )

    class Meta:
        ordering = ["-effective_from", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def is_published(self):
        return self.published_at is not None

    def cost_of(self, item):
        """This version's total unit cost for an item, or None."""
        row = self.costs.filter(item=item).first()
        return row.total if row is not None else None

    # -- rolling it up ---------------------------------------------------

    @transaction.atomic
    def roll_up(self, items=None):
        """
        Work out every made item's cost from the bought ones underneath
        it.

        Depth first with a path guard, exactly as `explode()` walks,
        because the graph loops: tape consumes regrind and throws
        regrind off. An item the walk is already inside is priced at
        what this version says it is bought or recovered for — which
        is also what the plant does, since the regrind going into a
        hopper came off the floor and not out of a fresh run.
        """
        if self.is_published():
            raise ValidationError(
                f"{self} was published on {self.published_on}. Published costs "
                "are what stock is valued at; roll up a new version instead."
            )
        from .bom import BillOfMaterials, default_bom_for

        wanted = items
        if wanted is None:
            wanted = [
                bom.item for bom in
                BillOfMaterials.objects.filter(is_default=True, is_active=True)
                .select_related("item")
            ]
        done = {}
        for item in wanted:
            self._cost(item, (), done)
        return done

    def _cost(self, item, path, done):
        """The unit cost of one item, computing what it rests on first."""
        from .bom import default_bom_for

        if item.pk in done:
            return done[item.pk]
        if item.pk in path:
            # A true loop in the components — A made from B made from A
            # — has no standard cost to compute: it is a simultaneous
            # equation, and every system that pretends otherwise is
            # quietly using a stale number for one side of it. A stated
            # price breaks it, which is what a plant does when it
            # decides regrind is worth sixty whatever it came out of.
            stated = self._stated_cost(item)
            if stated is None:
                raise ValidationError(
                    f"{item} is made from something that is made from "
                    f"{item} — {' → '.join(str(pk) for pk in path)} — so its "
                    "cost is a simultaneous equation with no answer. Give it "
                    "a price in this version to break the loop, which is what "
                    "the plant does when it decides what reground waste is "
                    "worth."
                )
            return stated
        bom = None if len(path) >= MAX_DEPTH else default_bom_for(item)
        if bom is None:
            return self._bought_cost(item)
        material = ZERO
        for component in bom.components.select_related("item", "uom").all():
            required = component.item.to_stock_quantity(
                component.gross_quantity(), component.uom
            )
            rate = self._cost(component.item, path + (item.pk,), done)
            material += rate * required
        conversion = self._conversion(bom)
        credit = self._byproduct_credit(bom, material + conversion)
        made = bom.item.to_stock_quantity(bom.quantity_produced, bom.uom)
        if made <= 0:
            raise ValidationError(
                f"{bom} says it makes nothing, so nothing it makes has a cost."
            )
        row, _created = StandardCost.objects.update_or_create(
            version=self, item=item,
            defaults={
                "material": (material / made).quantize(RATE),
                "conversion": (conversion / made).quantize(RATE),
                "byproduct_credit": (credit / made).quantize(RATE),
                "is_rolled": True, "bom": bom,
            },
        )
        row.total = row.material + row.conversion - row.byproduct_credit
        row.save(update_fields=["total", "updated_at"])
        done[item.pk] = row.total
        return row.total

    def _stated_cost(self, item):
        """A price somebody entered, here or on the item itself."""
        row = self.costs.filter(item=item, is_rolled=False).first()
        if row is not None:
            return row.total
        return item.standard_cost

    def _bought_cost(self, item):
        """
        What this version says a bought item costs.

        Entered, not derived. A standard is a decision — "we will cost
        polymer at ninety-four this year" — and taking it from whatever
        the last lorry happened to charge would make every quotation
        move with the spot market, which is the thing a standard exists
        to stop.
        """
        stated = self._stated_cost(item)
        if stated is not None:
            return stated
        raise ValidationError(
            f"{item} is bought and neither {self} nor the item itself says "
            "what it costs, so everything made from it would be priced as "
            "though it were free."
        )

    def _conversion(self, bom):
        if bom.routing_id is None:
            return ZERO
        batch = bom.quantity_produced
        total = ZERO
        for operation in bom.routing.operations.select_related("work_centre"):
            minutes = operation.minutes_for(batch, bom.uom)
            total += (
                minutes / MINUTES_PER_HOUR
                * operation.work_centre.conversion_rate_per_hour()
            )
        return total

    def _byproduct_credit(self, bom, run_cost):
        from .bom import byproduct_value

        credit = ZERO
        for byproduct in bom.byproducts.select_related("item", "uom").all():
            credit += byproduct_value(byproduct, byproduct.quantity, run_cost)
        return credit

    # -- publishing it ---------------------------------------------------

    @transaction.atomic
    def publish(self, on_date=None, by=None, memo=""):
        """
        Make this version the standard, and post what that does to the
        books.

        Stock valued at standard is worth `quantity x standard_cost`.
        Changing the standard therefore changes what every
        standard-costed shelf in the company is worth — instantly, and
        with nothing in the ledger to match it. Before this, the
        inventory account and the stock valuation simply parted company
        and nothing disagreed.

        So the difference is posted: the new value less the old, item
        by item, against the revaluation account. Only items actually
        costed at standard move — an item on weighted average is valued
        from its own movements and a standard change means nothing to
        it.
        """
        from apps.inventory.costing import CostingMethod
        from apps.inventory.valuation import inventory_account_for

        if self.is_published():
            raise ValidationError(f"{self} was already published on {self.published_on}.")
        rows = list(self.costs.select_related("item"))
        if not rows:
            raise ValidationError(
                f"{self} holds no costs, so publishing it would set every "
                "standard to nothing."
            )
        on_date = to_date(on_date) or timezone.now().date()

        postings = []
        changes = []
        for row in rows:
            item = row.item
            was = item.standard_cost or ZERO
            if row.total == was:
                # An optimisation and not a guard, said plainly because
                # the difference matters when somebody next reads this:
                # a nil change posts nothing anyway, since `_post_entry`
                # drops zero rows. This only saves writing a standard
                # cost back that already says what it said.
                continue
            changes.append((item, was, row.total))
            if item.costing_method != CostingMethod.STANDARD:
                # Valued from its own movements; a standard change means
                # nothing to what it is worth.
                continue
            # Across every shelf, and through the valuation replay
            # rather than `on_hand_at(None)` — that filters on
            # `warehouse=None` and answers nought for an item held
            # everywhere, which had the first version of this revalue
            # precisely nothing.
            on_hand, _value = item.valuation_at(None)
            if not on_hand:
                continue
            postings.append((
                inventory_account_for(item),
                round_money((row.total - was) * on_hand),
            ))

        entry = None
        if postings:
            # Through the same helper every other entry in this module
            # goes through, and with a balancing account, because
            # rounding a total and rounding its parts are different
            # numbers — a lesson this codebase has already paid for
            # once, on the first run with real prices in it. A second
            # way of writing a journal entry would be a second set of
            # bugs.
            from .orders import ManufacturingSettings, _post_entry

            account = ManufacturingSettings.account(
                "revaluation",
                "a standard cost change alters what every standard-costed "
                "shelf is worth",
            )
            entry, _taken = _post_entry(
                on_date, self.code,
                memo or f"Standard cost revaluation, {self}",
                postings, balance_to=account,
            )
        for item, _was, now in changes:
            item.standard_cost = now
            item.save(update_fields=["standard_cost", "updated_at"])

        self.published_at = timezone.now()
        self.published_on = on_date
        self.published_by = by
        self.revaluation_entry = entry
        self.save(update_fields=[
            "published_at", "published_on", "published_by",
            "revaluation_entry", "updated_at",
        ])
        return entry


class StandardCost(AuditModel):
    """
    One item's cost in one version, split into the three things it is
    made of.

    Kept apart because a sack whose cost doubled is a different problem
    depending on which of them moved: polymer at a different price, a
    loom running slower, or regrind suddenly worth nothing.
    """

    version = models.ForeignKey(
        CostVersion, on_delete=models.CASCADE, related_name="costs"
    )
    item = models.ForeignKey(
        "inventory.Item", on_delete=models.CASCADE, related_name="standard_costs"
    )
    material = models.DecimalField(
        max_digits=18, decimal_places=6, default=ZERO,
        help_text="Everything that goes in, at this version's prices, per "
                  "unit of output.",
    )
    conversion = models.DecimalField(
        max_digits=18, decimal_places=6, default=ZERO,
        help_text="Machine time at the work centres' rates.",
    )
    byproduct_credit = models.DecimalField(
        max_digits=18, decimal_places=6, default=ZERO,
        help_text="What comes off the line with it and is worth something.",
    )
    total = models.DecimalField(max_digits=18, decimal_places=6, default=ZERO)
    is_rolled = models.BooleanField(
        default=False,
        help_text="Computed from a bill of materials rather than entered. A "
                  "bought item's cost is a decision somebody made; a made "
                  "item's is arithmetic.",
    )
    bom = models.ForeignKey(
        "BillOfMaterials", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="The recipe it was rolled against, frozen: the recipe will "
                  "change and this version must not.",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["version", "item__sku"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "item"], name="one_cost_per_item_per_version"
            ),
        ]

    def __str__(self):
        return f"{self.item.sku} at {self.total} ({self.version.code})"

    def save(self, *args, **kwargs):
        if not self.is_rolled:
            # An entered cost is a single figure for something bought;
            # the split belongs to things that are made.
            self.total = self.material + self.conversion - self.byproduct_credit
        super().save(*args, **kwargs)

    def share_of(self, element):
        """What fraction of the total one element is, as a percentage."""
        if not self.total:
            return None
        value = getattr(self, element)
        return (value / self.total * Decimal("100")).quantize(Decimal("0.01"))


def explain(version, item):
    """
    A made item's cost broken down by what it is made of, one level
    at a time.

    The report that answers "why does a sack cost eleven rupees": so
    much fabric, so much lamination, so much ink, and how much of each
    of those is polymer. One level per call, because a planner reading
    a sack's cost wants the sack's own components first and the
    polymer three levels down only when they ask.
    """
    from .bom import default_bom_for

    row = version.costs.filter(item=item).first()
    bom = row.bom if row is not None else default_bom_for(item)
    if bom is None:
        return {
            "item": item, "cost": row.total if row else None,
            "bought": True, "lines": [],
        }
    made = bom.item.to_stock_quantity(bom.quantity_produced, bom.uom)
    lines = []
    for component in bom.components.select_related("item", "uom").all():
        required = component.item.to_stock_quantity(
            component.gross_quantity(), component.uom
        )
        rate = version.cost_of(component.item) or component.item.standard_cost or ZERO
        value = rate * required
        lines.append({
            "item": component.item,
            "quantity_per_unit": (required / made).quantize(RATE) if made else ZERO,
            "rate": rate,
            "cost_per_unit": (value / made).quantize(RATE) if made else ZERO,
            "share_percent": (
                (value / made / row.total * Decimal("100")).quantize(
                    Decimal("0.01")
                )
                if row and row.total and made else None
            ),
        })
    return {
        "item": item,
        "cost": row.total if row else None,
        "bought": False,
        "material": row.material if row else None,
        "conversion": row.conversion if row else None,
        "byproduct_credit": row.byproduct_credit if row else None,
        "lines": sorted(lines, key=lambda line: -line["cost_per_unit"]),
    }


def against_actual(version, warehouse=None):
    """
    The standard against what the shelf says, item by item.

    The report that says a standard has gone stale. A plant whose
    polymer standard is ninety-four and whose average is a hundred and
    nine has been reporting a favourable material variance on every
    run for months and calling it good buying.
    """
    rows = []
    for row in version.costs.select_related("item"):
        actual = row.item.average_cost_at(warehouse)
        rows.append({
            "item": row.item,
            "standard": row.total,
            "actual": actual,
            "difference": actual - row.total,
            "difference_percent": (
                ((actual - row.total) / row.total * Decimal("100")).quantize(
                    Decimal("0.01")
                )
                if row.total else None
            ),
        })
    return sorted(
        rows,
        key=lambda row: abs(row["difference_percent"] or ZERO), reverse=True,
    )
