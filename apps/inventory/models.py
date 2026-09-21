from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import models, transaction
from django.db.models import Q, Sum

from apps.core.models import AuditModel, UnitOfMeasure, to_date


class Warehouse(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    consignment_vendor = models.ForeignKey(
        "core.Party", null=True, blank=True, on_delete=models.PROTECT,
        related_name="consignment_warehouses",
        help_text="Set when the stock here belongs to a vendor until it is used. "
                  "It is on the premises and not on the books.",
    )
    is_quarantine = models.BooleanField(
        default=False,
        help_text="Holds goods received but not yet accepted. The stock is owned and "
                  "valued; it simply may not be shipped until someone has looked at it.",
    )
    receipt_route = models.CharField(
        max_length=16, default="direct",
        choices=[
            ("direct", "Straight to stock"),
            ("input", "Receiving bay, then stock"),
            ("inspect", "Receiving bay, then inspection, then stock"),
        ],
        help_text="How many places goods pass through on the way in. Declared here "
                  "rather than remembered on every order line, because it is a fact "
                  "about the building.",
    )
    input_warehouse = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="receives_for",
        help_text="The bay goods land in before they are put away. Stock here is "
                  "owned and valued and has not been put anywhere yet.",
    )
    quality_warehouse = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="inspects_for",
        help_text="Where goods wait to be looked at. Must be a quarantine "
                  "warehouse, which is what stops them being shipped.",
    )
    requires_bins = models.BooleanField(
        default=False,
        help_text="Every movement in or out must name a bin. Off by default, so "
                  "turning it on is a decision rather than something a warehouse "
                  "acquires by accident — and stock booked in before it was turned "
                  "on stays readable through unbinned().",
    )
    is_transit = models.BooleanField(
        default=False,
        help_text="Holds stock that has left one warehouse and not yet arrived at "
                  "another. Owned and valued like any other shelf — it is simply on "
                  "a lorry, so nothing can be picked from it.",
    )
    allow_negative_stock = models.BooleanField(
        default=False,
        help_text="Permit shipping more than is on hand (backorders, in-transit stock).",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def receipt_steps(self):
        """
        Every place arriving goods pass through, in order, ending here.

        One entry for a warehouse that receives straight to stock, which
        is what every warehouse did before this and what most still do.
        """
        if self.receipt_route == "input":
            return [self.input_warehouse, self]
        if self.receipt_route == "inspect":
            return [self.input_warehouse, self.quality_warehouse, self]
        return [self]

    def first_receipt_step(self):
        """Where arriving goods actually land."""
        return self.receipt_steps()[0]

    def next_receipt_step(self, after):
        """
        The place after `after` on the way in, or None at the end.

        Answered by position rather than by name, so a route that puts
        the same warehouse in twice still moves forward.
        """
        steps = self.receipt_steps()
        for index, step in enumerate(steps[:-1]):
            if step is not None and after is not None and step.pk == after.pk:
                return steps[index + 1]
        return None

    def clean(self):
        if self.receipt_route in ("input", "inspect") and self.input_warehouse is None:
            raise ValidationError(
                f"{self.code} receives through a bay and has not said which."
            )
        if self.receipt_route == "inspect" and self.quality_warehouse is None:
            raise ValidationError(
                f"{self.code} inspects on receipt and has not said where."
            )
        if self.quality_warehouse is not None and not self.quality_warehouse.is_quarantine:
            # Quarantine is what stops the goods being shipped. A quality
            # step that anybody can pick from is not a quality step.
            raise ValidationError(
                f"{self.quality_warehouse} is not a quarantine warehouse, so goods "
                "waiting there could be shipped before anyone looked at them."
            )
        for step, label in (
            (self.input_warehouse, "receiving bay"),
            (self.quality_warehouse, "inspection area"),
        ):
            if step is None:
                continue
            if self.pk and step.pk == self.pk:
                raise ValidationError(f"{self.code} cannot be its own {label}.")
            if step.receipt_route != "direct":
                # Otherwise arriving goods route into a bay that routes
                # them into another bay, and nothing says where they stop.
                raise ValidationError(
                    f"{step.code} is a {label} and must itself receive straight to "
                    "stock, or goods would route into it forever."
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class ItemType(models.TextChoices):
    GOODS = "goods", "Goods"
    SERVICE = "service", "Service"


class Item(AuditModel):
    template = models.ForeignKey(
        "ItemTemplate", null=True, blank=True, on_delete=models.PROTECT,
        related_name="variants",
        help_text="The product this is a variant of. Blank for a standalone item, "
                  "which is what everything was before variants existed and what "
                  "most items still are.",
    )
    variant_key = models.CharField(
        max_length=255, blank=True, editable=False,
        help_text="Canonical name for this variant's combination of attribute "
                  "values. Frozen at creation: changing a shirt's colour does not "
                  "change the shirt, it makes it a different product.",
    )
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    item_type = models.CharField(max_length=16, choices=ItemType.choices, default=ItemType.GOODS)
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="items")
    track_inventory = models.BooleanField(
        default=True,
        help_text="Services and non-stocked items should be False so they never affect stock levels.",
    )
    costing_method = models.CharField(
        max_length=16, default="average",
        choices=[
            ("average", "Weighted average"),
            ("fifo", "First in, first out"),
            ("standard", "Standard cost"),
            ("specific", "Specific identification"),
        ],
        help_text="How this item's stock is valued. Average needs no layer "
                  "bookkeeping and cannot be gamed by choosing which physical unit "
                  "to ship, which is why it is the default — but a company "
                  "reporting FIFO or running to a standard cannot bolt either on "
                  "later without restating every period.",
    )
    standard_cost = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="What a unit is deemed to cost under standard costing. Changing "
                  "it revalues the stock on hand, which is a posting, so change it "
                  "through set_standard_cost() rather than by assignment.",
    )
    tracking = models.CharField(
        max_length=8, default="none",
        choices=[("none", "Not tracked"), ("lot", "By lot or batch"), ("serial", "By serial number")],
        help_text="Whether each movement of this item must name the batch it came "
                  "from, or the individual unit. Defaults to neither, so nothing "
                  "already in the ledger is retrospectively incomplete.",
    )
    sale_price = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Default list price, used when no price list covers this item.",
    )
    inventory_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Asset account holding this item's stock value. Falls back to the company default.",
    )
    cogs_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Expense account charged when this item is sold. Falls back to the company default.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sku"]
        constraints = [
            # Two variants of one product with the same combination of
            # values are the same product twice, and every count, price
            # and reservation would then be split between them at random.
            models.UniqueConstraint(
                fields=["template", "variant_key"],
                condition=~Q(variant_key=""),
                name="one_variant_per_combination",
            ),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    def is_variant(self):
        return self.template_id is not None

    def variant_description(self):
        """"Red, Medium", or empty for a standalone item."""
        return ", ".join(str(row.value.name) for row in self.variant_values.all())

    def siblings(self):
        """The other variants of the same product."""
        if not self.is_variant():
            return Item.objects.none()
        return Item.objects.filter(template=self.template).exclude(pk=self.pk)

    def _check_costing_is_answerable(self):
        """
        Specific identification needs to know which goods left.

        Its entire premise is that each batch keeps its own cost, and an
        untracked item has no batches — so the method would have to guess,
        and a guess here is weighted average wearing another name and
        wrong by exactly the amount the method exists to get right.
        """
        if self.costing_method == "specific" and self.tracking == "none":
            raise ValidationError(
                f"{self.sku} is costed by specific identification, which needs to know "
                "which batch left. Track it by lot or serial number, or cost it "
                "another way."
            )

    def _check_matches_template(self):
        """
        A variant has to agree with its product about the things stock
        arithmetic depends on.

        Counting one variant in litres and another in bottles, or costing
        one FIFO and another at standard, makes the product-level total
        a sum of incompatible numbers — and that total is the only reason
        the template exists.
        """
        if not self.template_id:
            return
        template = self.template
        for field, label in (
            ("uom", "unit of measure"),
            ("tracking", "tracking"),
            ("costing_method", "costing method"),
        ):
            mine, theirs = getattr(self, field), getattr(template, field)
            mine = getattr(mine, "pk", mine)
            theirs = getattr(theirs, "pk", theirs)
            if mine != theirs:
                raise ValidationError(
                    f"{self.sku} is a variant of {template} and must share its "
                    f"{label}."
                )

    def _check_variant_key_frozen(self):
        if not self.pk:
            return
        previous = Item.objects.filter(pk=self.pk).values_list(
            "variant_key", "template_id"
        ).first()
        if previous is None:
            return
        was_key, was_template = previous
        if was_key and self.variant_key != was_key:
            raise ValidationError(
                f"{self.sku} is already a particular variant. Changing which one it "
                "is would rewrite the history of a different product."
            )
        if was_template and self.template_id != was_template:
            raise ValidationError(
                f"{self.sku} already belongs to a product and cannot be moved to "
                "another."
            )

    def save(self, *args, **kwargs):
        self._check_costing_is_answerable()
        self._check_matches_template()
        self._check_variant_key_frozen()
        super().save(*args, **kwargs)

    def on_hand_at(self, warehouse):
        """
        On-hand quantity is derived by summing movements, never stored, so it
        can never drift from the movement ledger that produced it.
        """
        total = self.movements.filter(warehouse=warehouse).aggregate(total=Sum("quantity"))["total"]
        return total or 0

    def _replay_valuation(self, warehouse=None, before_id=None, as_of=None):
        """
        Walk the movement ledger in order and return (quantity, value) at
        that point, by whichever method this item is costed under.

        Value is derived from the ledger rather than stored as a running
        field, for the same reason on-hand quantity is: a stored figure —
        an average, or a layer table — drifts the moment a movement is
        corrected, and nothing says so. The cost is an O(movements)
        replay, fine at this scale, and would want a periodic valuation
        snapshot at much larger volumes.
        """
        from .costing import replay

        return replay(self, warehouse, before_id, as_of)

    def cost_of_removing(self, warehouse, quantity, lot=None):
        """
        What taking `quantity` off this shelf will take off its value.

        Every outbound path asks this rather than multiplying a rate of
        its own, because under FIFO the units leaving may span layers
        bought at different prices and no single rate multiplies back to
        the right answer. A ledger entry that disagrees with the stock
        ledger by that difference disagrees permanently.

        `lot` is required under specific identification and ignored by
        every other method, which is the honest shape: the others have
        no use for it and that one cannot answer without it.
        """
        from .costing import cost_of_removing

        return cost_of_removing(self, warehouse, quantity, lot=lot)

    def removal_unit_cost(self, warehouse, quantity, lot=None):
        """`cost_of_removing` per unit, for movements that need a rate."""
        from .costing import unit_cost_for

        return unit_cost_for(self, warehouse, quantity, lot=lot)

    def average_cost_at(self, warehouse, before_id=None):
        """Weighted average unit cost, optionally as it stood before a movement."""
        quantity, value = self._replay_valuation(warehouse, before_id)
        if quantity <= 0:
            return Decimal("0")
        return (value / quantity).quantize(Decimal("0.0001"))

    def reserved_at(self, warehouse):
        """How much here is already promised to a document."""
        from .reservations import reserved_at

        return reserved_at(self, warehouse)

    def available_at(self, warehouse):
        """
        On hand, shippable, and not already promised to somebody else.

        Quarantined and in-transit stock is neither missing nor
        available: it is owned, valued, and not on a shelf anybody can
        pick from. Reserved stock is on the shelf and spoken for, which
        for anyone asking "can I promise this?" is the same answer.
        """
        if warehouse.is_quarantine or warehouse.is_transit or warehouse.consignment_vendor_id:
            return 0
        return self.on_hand_at(warehouse) - self.reserved_at(warehouse)

    def average_cost(self):
        """
        Weighted average across every warehouse: what a unit costs the
        company, whichever shelf it eventually ships from.

        A margin check at quoting time has no warehouse yet, and refusing
        to answer until one is chosen would make the check useless exactly
        when it matters — before the price is agreed.
        """
        return self.average_cost_at(None)

    def stock_value_at(self, warehouse):
        return self._replay_valuation(warehouse)[1].quantize(Decimal("0.01"))

    def valuation_at(self, warehouse, as_of=None):
        """
        (quantity, value) at full precision, unrounded.

        Anything that has to remove exactly what the replay will remove —
        a transfer's receiving leg, matching its despatching leg — needs
        the unrounded number. Rounding to a presentable two places first
        and multiplying back is how the two ends of a move stop agreeing.
        """
        return self._replay_valuation(warehouse, as_of=as_of)

    def to_stock_quantity(self, quantity, uom):
        """
        Restate a document quantity in this item's stocking unit.

        The ledger counts in one unit and one only. Ten cases and a
        hundred and twenty eaches are the same stock, and a ledger that
        holds both numbers as written can answer neither question.
        """
        if uom is None:
            return quantity
        return uom.convert_to(quantity, self.uom)

    def check_uom(self, uom):
        """
        Refuse a unit this item cannot be counted in, at the point someone
        types it rather than at the point stock moves.

        By the time a goods receipt posts, the order is confirmed and the
        goods are on the dock; the answer was knowable when the line was
        written.
        """
        if uom is None or uom.pk == self.uom_id:
            return
        uom.convert_to(Decimal("1"), self.uom)


class MovementType(models.TextChoices):
    RECEIPT = "receipt", "Receipt"
    ISSUE = "issue", "Issue"
    TRANSFER_IN = "transfer_in", "Transfer In"
    TRANSFER_OUT = "transfer_out", "Transfer Out"
    ADJUSTMENT = "adjustment", "Adjustment"


class StockMovement(AuditModel):
    """
    Append-only ledger of stock changes. On-hand quantity is always a
    derived aggregate of these rows (see Item.on_hand_at) rather than a
    separately stored counter, so the two can never disagree.
    """

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=16, choices=MovementType.choices)
    uom = models.ForeignKey(
        UnitOfMeasure, on_delete=models.PROTECT, related_name="+",
        help_text="The unit the document spoke in. Required on write; the quantity "
                  "stored alongside it has already been restated in the item's "
                  "stocking unit, so the ledger only ever counts in one unit.",
    )
    document_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True, editable=False,
        help_text="The quantity as the document wrote it, in `uom`. Kept so a "
                  "movement can be read back against the paperwork that caused it.",
    )
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="In the item's stocking unit. Positive for inbound movements "
                  "(receipt, transfer_in), negative for outbound.",
    )
    unit_cost = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="Cost per unit for this movement; set from the purchase price inbound, "
                  "from the weighted average outbound.",
    )
    adjusts = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="adjustments",
        help_text="On a value-only movement, the inbound movement whose goods the "
                  "value belongs to. Landed cost knows which receipt it was "
                  "incurred on; under FIFO that decides which layer it raises, and "
                  "spreading it over the shelf instead would misprice everything "
                  "that was already there.",
    )
    value_adjustment = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="Value added to (or taken off) the stock without moving any "
                  "quantity — landed cost, and the remainder a conversion or a "
                  "transfer cannot express in a unit cost. Four places, not two: "
                  "a posted amount is money and rounds to the cent, but this also "
                  "carries what is left when a value is divided by a quantity, and "
                  "rounding that to the cent loses a little on every move.",
    )
    bin = models.ForeignKey(
        "StorageBin", null=True, blank=True, on_delete=models.PROTECT, related_name="movements",
        help_text="Where in the building. Required when the warehouse says so. "
                  "Valuation ignores it: a bin changes where stock is, not what "
                  "it is worth.",
    )
    lot = models.ForeignKey(
        "Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="movements",
        help_text="Which batch, or which unit. Required when the item is tracked, "
                  "refused when it is not.",
    )
    reference = models.CharField(max_length=64, blank=True, help_text="e.g. PO number, SO number")
    occurred_at = models.DateTimeField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.movement_type} {self.quantity} {self.item.sku} @ {self.warehouse.code}"

    def save(self, *args, **kwargs):
        """
        Restate the movement in the item's stocking unit, here and nowhere
        else.

        `uom` is the unit that BOTH `quantity` and `unit_cost` are given
        in, and it is required on write. Every caller therefore answers
        one question — what unit are my numbers in? — and a caller already
        working in stocking units answers by naming the item's own unit
        rather than by staying silent and being guessed at. A goods
        receipt answers with the order line's unit, because its cost is
        the agreed price per that unit. A delivery answers with the
        stocking unit, because its cost is the weighted average, which is
        a fact this ledger holds per stocking unit.

        Eleven places across purchasing and sales write here. Converting
        at each would be eleven conversions and the twelfth would be
        written without one — the same argument that puts the period lock
        inside JournalEntry.post().

        Total value is held fixed rather than the unit cost being divided
        by the factor. Ten cases at 60 is 600, and 600 over 120 eaches is
        exactly 5; dividing 60 by 12 agrees here and would not for a
        factor that does not divide the price evenly.
        """
        if self._state.adding:
            self._check_bin()
            self._check_tracking()
            if self.uom_id is None:
                raise ValidationError(
                    f"A stock movement for {self.item} must say which unit its "
                    "quantity is in, even when that is the item's own."
                )
            if self.document_quantity is None:
                self.document_quantity = self.quantity
            if self.uom_id != self.item.uom_id:
                gross = self.quantity * (self.unit_cost or Decimal("0"))
                self.quantity = self.item.to_stock_quantity(
                    self.quantity, self.uom
                ).quantize(Decimal("0.0001"))
                if self.unit_cost is not None:
                    self.unit_cost = (
                        (gross / self.quantity).quantize(Decimal("0.0001"))
                        if self.quantity
                        else Decimal("0")
                    )
                    # A hundred cases at 7 is 700, and 700 over 1200 eaches
                    # is 0.58333... A unit cost has four decimal places, so
                    # quantity times cost comes back four cents short and
                    # the stock ledger drifts from the bill the GL posted.
                    # The residue is value with no quantity attached, which
                    # is what value_adjustment already means — and it is
                    # only inbound that unit_cost sets the value at all:
                    # going out, the replay uses the running average and
                    # ignores the cost entirely.
                    if self.quantity > 0:
                        residue = (gross - self.quantity * self.unit_cost).quantize(
                            Decimal("0.0001")
                        )
                        if residue:
                            self.value_adjustment = (
                                self.value_adjustment or Decimal("0")
                            ) + residue
        super().save(*args, **kwargs)


# Stock adjustments and counts live in their own module because they are
# documents with a posting path, not ledger primitives — but Django only
# discovers models that this one pulls in. The import is last so that
# Item, Warehouse and StockMovement are fully defined before adjustments
# imports them back.
    def _check_bin(self):
        """
        A bin must be in this movement's warehouse, and must be somewhere
        stock can actually sit.
        """
        if self.bin_id is not None:
            if self.bin.warehouse_id != self.warehouse_id:
                raise ValidationError(
                    f"{self.bin} is not in {self.warehouse}."
                )
            if not self.bin.is_pickable:
                raise ValidationError(
                    f"{self.bin} groups other bins rather than holding stock; name "
                    "one of the places inside it."
                )
        elif self.warehouse.requires_bins:
            raise ValidationError(
                f"{self.warehouse} is binned; this movement must say where in it "
                "the stock is."
            )

    def _check_tracking(self):
        """
        A tracked item's movement must name its lot, and an untracked
        one's must not.

        Here rather than on each document, for the same reason the unit
        conversion is here: eleven writers, and the twelfth would forget.
        A lot that is merely optional is a lot that is absent exactly
        when somebody needs it.
        """
        tracking = self.item.tracking
        if tracking == "none":
            if self.lot_id is not None:
                raise ValidationError(
                    f"{self.item} is not tracked by lot or serial number, so this "
                    "movement cannot name one."
                )
            return
        if self.lot_id is None:
            raise ValidationError(
                f"{self.item} is tracked by "
                f"{'serial number' if tracking == 'serial' else 'lot'}; this movement "
                "must say which."
            )
        if self.lot.item_id != self.item_id:
            raise ValidationError(f"Lot {self.lot.code} does not belong to {self.item}.")
        if tracking != "serial":
            # A shelf holding a hundred across three batches holds ten of
            # this one. Checking the shelf total and not the batch lets a
            # delivery ship a batch that ran out, and the trail then says
            # a customer received goods that were never there.
            if self.quantity < 0 and not self.warehouse.allow_negative_stock:
                held = self.lot.on_hand_at(self.warehouse)
                wanted = -self.item.to_stock_quantity(self.quantity, self.uom)
                if wanted > held:
                    raise ValidationError(
                        f"Only {held} of batch {self.lot.code} at {self.warehouse}; "
                        f"cannot move {wanted}."
                    )
            return

        # A serial number identifies one unit. Two of it is not a larger
        # quantity of the same thing, it is a contradiction, and the only
        # moment it can be refused is before the movement is written.
        if abs(self.quantity) != 1:
            raise ValidationError(
                f"{self.lot.code} is a serial number, so it moves one unit at a time, "
                f"not {self.quantity}."
            )
        if self.quantity > 0 and self.lot.on_hand_at() > 0:
            raise ValidationError(
                f"Serial number {self.lot.code} is already in stock at "
                f"{self.lot.warehouses()[0][0]}; the same unit cannot arrive twice."
            )
        if self.quantity < 0 and self.lot.on_hand_at(self.warehouse) < 1:
            raise ValidationError(
                f"Serial number {self.lot.code} is not at {self.warehouse}."
            )


@transaction.atomic
def set_standard_cost(item, new_cost, warehouse=None, on_date=None, reason=None):
    """
    Change an item's standard cost and revalue the stock on hand.

    A standard cost is what the books say a unit is worth, so changing
    it changes what the shelf is worth — by definition, immediately, and
    for every unit already there. Assigning the field and walking away
    would leave the inventory account saying one number and the stock
    ledger another, which is the drift this codebase derives everything
    to avoid.

    The revaluation is written as an adjustment, so it reverses the same
    way everything else here does.
    """
    from .adjustments import AdjustmentReason, StockAdjustment, StockAdjustmentLine
    from apps.core.models import Company

    new_cost = Decimal(new_cost)
    if item.costing_method != "standard":
        raise ValidationError(
            f"{item} is not costed at standard, so it has no standard to change."
        )
    if new_cost <= 0:
        raise ValidationError("A standard cost must be positive.")

    on_date = to_date(on_date) or timezone.now().date()
    old_cost = item.standard_cost or Decimal("0")
    warehouses = (
        [warehouse] if warehouse is not None
        else list(Warehouse.objects.filter(movements__item=item).distinct())
    )

    raised = []
    for shelf in warehouses:
        held = item.on_hand_at(shelf)
        if held == 0:
            continue
        difference = (held * (new_cost - old_cost)).quantize(Decimal("0.0001"))
        if not difference:
            continue
        if reason is None:
            raise ValidationError(
                "Revaluing stock posts to the ledger; say which reason account it "
                "belongs to."
            )
        adjustment = StockAdjustment.objects.create(
            adjustment_date=on_date, warehouse=shelf, reason=reason,
            memo=f"Standard cost of {item} from {old_cost} to {new_cost}",
            from_standard_change=True,
        )
        # Quantity is unchanged; only the value moves. The adjustment line
        # carries it as a value-only movement, which is what
        # value_adjustment already means here.
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=item, uom=item.uom,
            quantity=Decimal("0"), revaluation=difference,
            notes=f"{held} on hand revalued by {new_cost - old_cost} each",
        )
        raised.append(adjustment)

    item.standard_cost = new_cost
    super(Item, item).save(update_fields=["standard_cost", "updated_at"])
    for adjustment in raised:
        adjustment.post()
    return raised


from .costing import CostingMethod  # noqa: E402,F401
from .bins import (  # noqa: E402,F401
    StorageBin,
    bins_holding,
    suggest_pick,
    suggest_putaway,
    unbinned,
)
from .variants import (  # noqa: E402,F401
    ItemAttribute,
    ItemAttributeValue,
    ItemTemplate,
    ItemVariantValue,
    variant_key,
)
from .locking import (  # noqa: E402,F401
    StockPosition,
    lock_position,
    lock_positions,
)
from .reports import (  # noqa: E402,F401
    movement_summary,
    negative_stock,
    reconcile_to_ledger,
    slow_moving,
    stock_aging,
    stock_ledger,
    stock_valuation,
)
from .picking import (  # noqa: E402,F401
    describe_plan,
    plan_issue,
    plan_putaway,
)
from .tracking import (  # noqa: E402,F401
    Lot,
    TrackingMode,
    allocate,
    expiring,
    lots_at,
    traceability,
)
from .adjustments import (  # noqa: E402,F401
    AdjustmentDirection,
    AdjustmentReason,
    StockAdjustment,
    StockAdjustmentLine,
    StockCount,
    StockCountLine,
)
from .reservations import (  # noqa: E402,F401
    StockReservation,
    release_for,
    reserved_at,
)
from .transfers import (  # noqa: E402,F401
    StockTransfer,
    StockTransferLine,
    StockTransferStep,
    TransferStatus,
)
