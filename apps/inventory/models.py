from django.db import models
from django.db.models import Sum

from apps.core.models import AuditModel, UnitOfMeasure


class Warehouse(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
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
    reference = models.CharField(max_length=64, blank=True, help_text="e.g. PO number, SO number")
    occurred_at = models.DateTimeField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.movement_type} {self.quantity} {self.item.sku} @ {self.warehouse.code}"
