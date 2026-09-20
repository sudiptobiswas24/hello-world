from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from apps.core.models import AuditModel, UnitOfMeasure


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
    allow_negative_stock = models.BooleanField(
        default=False,
        help_text="Permit shipping more than is on hand (backorders, in-transit stock).",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ItemType(models.TextChoices):
    GOODS = "goods", "Goods"
    SERVICE = "service", "Service"


class Item(AuditModel):
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    item_type = models.CharField(max_length=16, choices=ItemType.choices, default=ItemType.GOODS)
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="items")
    track_inventory = models.BooleanField(
        default=True,
        help_text="Services and non-stocked items should be False so they never affect stock levels.",
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

    def __str__(self):
        return f"{self.sku} - {self.name}"

    def on_hand_at(self, warehouse):
        """
        On-hand quantity is derived by summing movements, never stored, so it
        can never drift from the movement ledger that produced it.
        """
        total = self.movements.filter(warehouse=warehouse).aggregate(total=Sum("quantity"))["total"]
        return total or 0

    def _replay_valuation(self, warehouse=None, before_id=None):
        """
        Walk the movement ledger in order, maintaining running quantity and
        value, and return (quantity, value) at that point.

        Weighted average is derived from the ledger rather than stored as a
        running field, for the same reason on-hand quantity is: a stored
        average drifts the moment a movement is corrected. The cost is an
        O(movements) replay, which is fine at this scale and would want a
        periodic valuation snapshot at much larger volumes.
        """
        movements = self.movements.all()
        if warehouse is not None:
            movements = movements.filter(warehouse=warehouse)
        if before_id is not None:
            movements = movements.filter(id__lt=before_id)

        quantity = Decimal("0")
        value = Decimal("0")
        for movement in movements.order_by("occurred_at", "id"):
            if movement.quantity > 0:
                unit_cost = movement.unit_cost or Decimal("0")
                value += movement.quantity * unit_cost
                quantity += movement.quantity
            elif movement.quantity < 0:
                leaving = -movement.quantity
                average = (value / quantity) if quantity > 0 else Decimal("0")
                value -= leaving * average
                quantity -= leaving
            # A value-only movement changes what the stock is worth without
            # changing how much there is, which is exactly what landed cost
            # does. Replaying it here rather than storing a corrected
            # average keeps valuation derived, like everything else.
            if movement.value_adjustment:
                value += movement.value_adjustment
        return quantity, value

    def average_cost_at(self, warehouse, before_id=None):
        """Weighted average unit cost, optionally as it stood before a movement."""
        quantity, value = self._replay_valuation(warehouse, before_id)
        if quantity <= 0:
            return Decimal("0")
        return (value / quantity).quantize(Decimal("0.0001"))

    def available_at(self, warehouse):
        """
        On hand and shippable. Quarantined stock is neither missing nor
        available: it is owned, valued and not yet cleared.
        """
        if warehouse.is_quarantine or warehouse.consignment_vendor_id:
            return 0
        return self.on_hand_at(warehouse)

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
    value_adjustment = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Money added to (or taken off) the stock value without moving any "
                  "quantity — landed cost, mainly. Raises the weighted average.",
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
                            Decimal("0.01")
                        )
                        if residue:
                            self.value_adjustment = (
                                self.value_adjustment or Decimal("0")
                            ) + residue
        super().save(*args, **kwargs)
