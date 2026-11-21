"""
What a bale takes besides its bags: the cover it is pressed into, the
straps round it, the label on it. A sack's packing recipe says how much
of each a bale of it takes; pressing a bale draws them from the shelf
under the packing reason, so stores' covers move and their cost lands
where that reason says. A sack with no recipe packs with nothing drawn,
and the bale says so.

Breaking a bale does not put them back: a cut strap is a cut strap.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import AuditModel, to_date

BALE_PACKING = "bale packing"
ZERO = Decimal("0")
PAISA = Decimal("0.01")


class PackingLine(AuditModel):
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="packing_lines",
                             help_text="The sack whose bales take this.")
    packing_item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+",
                                     help_text="The cover, the straps or the label.")
    quantity = models.DecimalField(max_digits=18, decimal_places=4,
                                   help_text="How much of it one bale takes, in the packing item's own unit.")

    class Meta:
        ordering = ["item", "packing_item"]
        constraints = [
            models.UniqueConstraint(fields=["item", "packing_item"], name="one_packing_line_a_material"),
            models.CheckConstraint(check=Q(quantity__gt=0), name="packing_line_takes_something"),
        ]

    def __str__(self):
        return f"{self.item.sku}: {self.quantity} {self.packing_item.sku} a bale"

    def save(self, *args, **kwargs):
        if self.item_id == self.packing_item_id:
            raise ValidationError({"packing_item": "A sack does not pack itself."})
        if self.quantity <= 0:
            raise ValidationError({"quantity": "A bale takes more than nothing of it."})
        super().save(*args, **kwargs)


class BalePacking(AuditModel):
    """The adjustment that drew a bale's packing from the shelf: the fact of what it took, at what it cost."""

    bale = models.OneToOneField("manufacturing.Bale", on_delete=models.PROTECT, related_name="packing")
    adjustment = models.OneToOneField("inventory.StockAdjustment", on_delete=models.PROTECT, related_name="+")

    def __str__(self):
        return f"Packing for {self.bale}"

    def lines(self):
        """[(packing item, quantity drawn)]."""
        return [(line.item, -line.quantity) for line in self.adjustment.lines.all()]

    def cost(self):
        """What the packing cost at the moment it was drawn, as the movements recorded it."""
        return sum((-line.quantity * line.movement.unit_cost
                    for line in self.adjustment.lines.all() if line.movement_id),
                   ZERO).quantize(PAISA)


def consume_packing(bale, on_date):
    """Draw a bale's cover, straps and label from its warehouse under the packing reason; None where the sack has no recipe."""
    from apps.inventory.adjustments import StockAdjustment, StockAdjustmentLine

    from .orders import ManufacturingSettings

    recipe = list(PackingLine.objects.filter(item=bale.item).select_related("packing_item__uom"))
    if not recipe:
        return None
    reason = ManufacturingSettings.get().packing_reason
    if reason is None:
        raise ValidationError("Say which adjustment reason packing material is written off under, "
                              "in the manufacturing settings.")
    adjustment = StockAdjustment.objects.create(
        adjustment_date=to_date(on_date), warehouse=bale.warehouse, reason=reason,
        memo=f"Packing for {bale}"[:255], raised_by=BALE_PACKING,
    )
    for line in recipe:
        StockAdjustmentLine.objects.create(adjustment=adjustment, item=line.packing_item,
                                           uom=line.packing_item.uom, quantity=-line.quantity)
    adjustment.post()
    return BalePacking.objects.create(bale=bale, adjustment=adjustment)
