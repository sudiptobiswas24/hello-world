from decimal import Decimal

from django.db import models
from django.db.models import Sum

from apps.core.models import AuditModel, UnitOfMeasure


class Warehouse(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
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
        if warehouse.is_quarantine:
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
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Positive for inbound movements (receipt, transfer_in), negative for outbound.",
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
