"""
Bales: bundles of counted bags pressed, strapped and labelled for the
lorry, and the number a customer quotes when one is wrong.

**A bale is a labelled container, not a batch of its own.** Its stock
stays in the bundles it holds, each a batch with its own weight
inspection (conversion.py). Stock, the release gate, certificates and
recalls already work per bundle; a bale that became a new batch would
have to move the stock into itself and re-derive its release from what
it held, two records of one fact that could come to disagree. So a bale
names its bundles and how many bags of each, and everything else is
read through them.

**Packed and sealed at once**, as at the press, and numbered then. A
bundle goes into a bale only if quality has released it and only as far
as it has bags on the shelf not already in another bale. Every bundle
in a bale is the same item.

**Shipped is read, not stored.** Loading a bale onto a draft delivery
writes one delivery line per bundle and the bale remembers the delivery
(the bale is the dependent side). It can be unloaded until the delivery
posts; once it has, the bale has shipped, and the customer and the
delivery are read from it. A bale may be broken, its bundles freed for
another, only while it is on no delivery.

**Traced both ways from its number**: back through its bundles to the
weights, machines and operators, and on through the runs to the polymer;
forward to who was shipped it. A recall of any batch lists the bales
holding what it touched, because the bale number is what the customer's
warehouse can see.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, to_date

ZERO = Decimal("0")
GRAMMES_PER_KG = Decimal("1000")


class Bale(AuditModel):
    number = models.CharField(max_length=32, editable=False)
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="bales")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT,
                                  related_name="bales")
    packed_on = models.DateField()
    packed_by = models.ForeignKey("hr.Employee", on_delete=models.PROTECT,
                                  related_name="bales_packed")
    gross_kg = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True,
        help_text="What the bale weighed on the floor scale, strapping and all, "
                  "where it was weighed.")
    delivery = models.ForeignKey("sales.Delivery", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="bales",
                                 editable=False)
    broken_at = models.DateTimeField(null=True, blank=True, editable=False)
    broken_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-packed_on", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""),
                                    name="bale_number_unique"),
        ]

    def __str__(self):
        return self.number

    def bags(self):
        return sum((line.quantity for line in self.lines.all()), ZERO)

    def nominal_kg(self, on_date=None):
        """The bags at the weight they were checked against, in kilogrammes."""
        from .conversion import BagCount

        total = ZERO
        for line in self.lines.select_related("lot"):
            count = BagCount.objects.filter(inspection__lot=line.lot).first()
            if count is not None:
                total += line.quantity * count.target_grams / GRAMMES_PER_KG
        return total.quantize(Decimal("0.001"))

    def status(self):
        if self.broken_at is not None:
            return "broken"
        if self.delivery_id is None:
            return "sealed"
        if not self.matches_delivery():
            # The delivery's lines are sales' to edit, and sales cannot
            # know about bales. Rather than call a bale shipped that went
            # short, it says what it holds and what the lorry carried differ.
            return "altered"
        return "shipped" if self.delivery.posted else "loaded"

    def matches_delivery(self):
        """Whether each bundle's delivery line still carries exactly what the bale holds."""
        for line in self.lines.select_related("delivery_line"):
            carried = line.delivery_line
            if (carried is None or carried.delivery_id != self.delivery_id
                    or carried.lot_id != line.lot_id or carried.quantity_shipped != line.quantity):
                return False
        return True

    def save(self, *args, **kwargs):
        if not self._state.adding and not getattr(self, "_moving", False):
            raise ValidationError(f"{self} is sealed. Break it and pack another.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self} is sealed; break it.")

    def _write(self, fields):
        self._moving = True
        try:
            self.save(update_fields=fields + ["updated_at"])
        finally:
            self._moving = False


class BaleLine(AuditModel):
    bale = models.ForeignKey(Bale, on_delete=models.CASCADE, related_name="lines")
    lot = models.ForeignKey("inventory.Lot", on_delete=models.PROTECT, related_name="bale_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=4,
                                   help_text="Bags of this bundle in the bale.")
    delivery_line = models.ForeignKey("sales.DeliveryLine", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="+",
                                      editable=False)

    class Meta:
        ordering = ["bale", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="bale_line_positive"),
        ]

    def __str__(self):
        return f"{self.quantity} of {self.lot.code} in {self.bale}"

    def save(self, *args, **kwargs):
        # Sealed with the bale: the only thing that changes afterwards is
        # which delivery line carried it, written by load and unload.
        if not self._state.adding and set(kwargs.get("update_fields") or ()) != {
                "delivery_line", "updated_at"}:
            raise ValidationError(f"{self.bale} is sealed. Break it and pack another.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self.bale} is sealed; break it.")


def _standing(lines):
    """Lines of bales not broken and not shipped: bags spoken for on the shelf."""
    return lines.filter(bale__broken_at__isnull=True).exclude(
        bale__delivery__posted=True)


def baled(lot):
    """Bags of this batch sealed in bales still on the shelf: spoken for."""
    return sum((line.quantity for line in _standing(BaleLine.objects.filter(lot=lot))), ZERO)


def check_not_baled(lot, what):
    """
    Refuse `what` while bags of this batch are sealed in a bale. A bundle
    voided from under its bale left the bale reading 1,000 bags, sealed,
    with 500 of them gone.
    """
    held = baled(lot) if lot is not None else ZERO
    if held:
        raise ValidationError(
            f"{format(held.normalize(), 'f')} of {lot.code} are sealed in a bale; break "
            f"the bale before {what}."
        )


@transaction.atomic
def pack(warehouse, packed_by, rows, on_date=None, gross_kg=None):
    """Press, strap and seal a bale of [(bundle lot, bags)]."""
    from apps.inventory.locking import lock_positions
    from apps.quality.release import check_released

    on_date = to_date(on_date) or timezone.localdate()
    rows = [(lot, Decimal(str(quantity))) for lot, quantity in rows]
    if not rows:
        raise ValidationError("A bale with nothing in it holds nothing.")
    if not packed_by.is_working_on(on_date):
        raise ValidationError(f"{packed_by} does not work here on {on_date}.")
    items = {lot.item_id for lot, _ in rows}
    if len(items) != 1:
        raise ValidationError("Every bundle in a bale is the same sack.")
    item = rows[0][0].item
    lock_positions([(item, warehouse)])
    asked = defaultdict(lambda: ZERO)
    for lot, quantity in rows:
        if quantity <= 0 or quantity != quantity.to_integral_value():
            raise ValidationError(f"{lot.code}: bags are counted whole, and at least one.")
        check_released(item, lot, action="be baled")
        asked[lot] += quantity
    for lot, quantity in asked.items():
        free = lot.on_hand_at(warehouse) - baled(lot)
        if quantity > free:
            raise ValidationError(
                f"{lot.code} has {format(free.normalize(), 'f')} bags on the shelf not already "
                f"in a bale; {format(quantity.normalize(), 'f')} were asked for."
            )
    if gross_kg is not None:
        gross_kg = Decimal(str(gross_kg))
        if gross_kg <= 0:
            raise ValidationError("A bale weighs something.")
    bale = Bale.objects.create(
        number=DocumentSequence.next_for("manufacturing.bale", on_date, name="Bales",
                                         prefix="BL-"),
        item=item, warehouse=warehouse, packed_on=on_date, packed_by=packed_by,
        gross_kg=gross_kg,
    )
    for lot, quantity in asked.items():
        BaleLine.objects.create(bale=bale, lot=lot, quantity=quantity)
    # The cover, straps and label come off the shelf with the bale; refused, the bale is too.
    from .packing import consume_packing

    consume_packing(bale, on_date)
    return bale


@transaction.atomic
def break_bale(bale, reason):
    """Cut the straps: its bundles are free for another bale."""
    if bale.broken_at is not None:
        raise ValidationError(f"{bale} is already broken.")
    if bale.delivery_id is not None:
        raise ValidationError(
            f"{bale} is on {bale.delivery}"
            + (", which has shipped." if bale.delivery.posted else "; unload it first.")
        )
    reason = " ".join((reason or "").split())
    if not reason:
        raise ValidationError("Say why the bale is broken.")
    bale.broken_at, bale.broken_reason = timezone.now(), reason
    bale._write(["broken_at", "broken_reason"])
    return bale


@transaction.atomic
def load(delivery, bales, order_line=None):
    """Put sealed bales on a draft delivery: a line per bundle."""
    from apps.sales.models import DeliveryLine

    if delivery.posted:
        raise ValidationError(f"{delivery} has shipped; nothing more goes on it.")
    if delivery.is_return():
        raise ValidationError(f"{delivery} is goods coming back.")
    for bale in bales:
        bale = Bale.objects.select_for_update().get(pk=bale.pk)
        if bale.broken_at is not None:
            raise ValidationError(f"{bale} was broken.")
        if bale.delivery_id is not None:
            raise ValidationError(f"{bale} is already on {bale.delivery}.")
        line_for = order_line
        if line_for is None:
            matches = list(delivery.sales_order.lines.filter(item=bale.item))
            if len(matches) != 1:
                raise ValidationError(
                    f"{delivery.sales_order} has {len(matches) or 'no'} line"
                    f"{'s' if len(matches) > 1 else ''} for {bale.item}; say which."
                )
            line_for = matches[0]
        if line_for.order_id != delivery.sales_order_id or line_for.item_id != bale.item_id:
            raise ValidationError(f"{line_for} is not a line for {bale.item} on this order.")
        for row in bale.lines.select_related("lot"):
            row.delivery_line = DeliveryLine.objects.create(
                delivery=delivery, order_line=line_for, warehouse=bale.warehouse,
                quantity_shipped=row.quantity, lot=row.lot,
            )
            row.save(update_fields=["delivery_line", "updated_at"])
        bale.delivery = delivery
        bale._write(["delivery"])


@transaction.atomic
def unload(bale):
    """Take a bale off a delivery that has not shipped."""
    bale = Bale.objects.select_for_update().get(pk=bale.pk)
    if bale.delivery_id is None:
        raise ValidationError(f"{bale} is on no delivery.")
    if bale.delivery.posted:
        raise ValidationError(f"{bale} has shipped on {bale.delivery}; a return brings it back.")
    for row in bale.lines.exclude(delivery_line=None).select_related("delivery_line"):
        line = row.delivery_line
        row.delivery_line = None
        row.save(update_fields=["delivery_line", "updated_at"])
        line.delete()
    bale.delivery = None
    bale._write(["delivery"])
    return bale


def trace(bale):
    """Back to the polymer and forward to the customer, from a bale's number."""
    from .conversion import BagCount
    from .demand import genealogy

    bundles = []
    for line in bale.lines.select_related("lot"):
        count = BagCount.objects.filter(inspection__lot=line.lot).select_related(
            "machine", "operator__party", "inspection", "work_order").first()
        bundles.append({
            "lot": line.lot.code, "bags": line.quantity,
            "machine": count.machine.code if count else None,
            "operator": count.operator.party.name if count else None,
            "run": count.work_order.number if count else None,
            "inspection": count.inspection.number if count else None,
            "mean_grams": count.sample_mean_grams if count else None,
            "target_grams": count.target_grams if count else None,
            "made_from": [{"lot": step["from_lot"].code, "item": step["from_lot"].item.sku,
                           "level": step["level"]} for step in genealogy(line.lot)],
        })
    delivery = bale.delivery
    shipped = delivery if delivery is not None and delivery.posted else None
    return {
        "bale": bale.number, "item": bale.item.sku, "status": bale.status(),
        "bags": bale.bags(), "packed_on": bale.packed_on, "packed_by": bale.packed_by.party.name,
        "bundles": bundles,
        "shipped_to": shipped.sales_order.customer.name if shipped else None,
        "delivery": shipped.number if shipped else None,
    }


def bales_holding(lots):
    """Bales, not broken, that hold any of these batches, and where each went."""
    rows = []
    seen = set()
    for line in BaleLine.objects.filter(lot__in=list(lots), bale__broken_at__isnull=True
                                        ).select_related("bale__delivery__sales_order__customer",
                                                         "lot"):
        bale = line.bale
        if bale.pk in seen:
            continue
        seen.add(bale.pk)
        shipped = bale.delivery if bale.delivery_id and bale.delivery.posted else None
        rows.append({"bale": bale.number, "status": bale.status(),
                     "customer": shipped.sales_order.customer.name if shipped else None,
                     "delivery": shipped.number if shipped else None})
    return sorted(rows, key=lambda row: row["bale"])
