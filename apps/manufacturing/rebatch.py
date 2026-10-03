"""
One batch made into several, or several into one.

A bundle of 500 bags half rejected at a customer's inspection is two
batches from then on: the 250 that passed and the 250 going back to be
re-sorted. Three short ends of a print run are one pallet. Neither is a
run — nothing is made — and neither may lose where the sacks came
from.

**A re-batch is a document**, not an edit to a lot: so much out of
each batch it takes, so much into each new one, the same sack on the
same shelf, and what goes in comes out. It moves stock between batches
at the cost it came off at, so the shelf is worth the same after. The
movements are transfers rather than receipts and issues, because the
latest receipt is what a quote prices material at, and a re-batch is
not a purchase.

**The new batches remember their sources**, and everything that asks
where a batch came from asks through them: the runs that made it
(customer ownership, material rules, genealogy, certificate ancestry)
and the batches made from it (recall).

**A new batch carries its sources' standing until it has its own.**
With no inspection of its own, it is released only if every source is
released, held if any is held, and not yet inspected otherwise. Mixing
a held batch into released ones holds the lot of them; it never passes
the held sacks. It expires when its soonest-expiring source does.

**A customer's inspector released the source, not the new batch.** A
third-party release names batches, and a re-batched sack must be
offered again.

**Refused:** a batch already in a sealed bale (break the bale first); a
new batch that has ever held stock (a re-batch makes batches, it does
not top them up); the consignor's stock; serial-numbered items; one
batch into one batch, which is the same batch.

**Voided** while the new batches are untouched: nothing has moved them
since, and none of them is in a bale.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, to_date

ZERO = Decimal("0")


def _q(value):
    return format(Decimal(value).normalize(), "f")


class Rebatch(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT,
                                  related_name="+")
    rebatched_on = models.DateField()
    reason = models.CharField(max_length=255)
    posted = models.BooleanField(default=False, editable=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-rebatched_on", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""),
                                    name="rebatch_number_unique"),
        ]

    def __str__(self):
        return self.number or f"Draft re-batch {self.pk}"

    def save(self, *args, **kwargs):
        if self.pk and Rebatch.objects.filter(pk=self.pk, posted=True).exists() \
                and not getattr(self, "_writing", False):
            raise ValidationError(f"{self} is posted. Void it and re-batch again.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError(f"{self} is posted; void it.")
        return super().delete(*args, **kwargs)

    def _write(self, fields):
        self._writing = True
        try:
            self.save(update_fields=fields + ["updated_at"])
        finally:
            # Left set, the next save of this object would pass the guard too.
            self._writing = False

    def taken(self):
        return self.lines.filter(side=RebatchSide.IN).select_related("lot")

    def made(self):
        return self.lines.filter(side=RebatchSide.OUT).select_related("lot")

    @transaction.atomic
    def post(self):
        from apps.inventory.locking import lock_positions
        from apps.inventory.models import MovementType, StockMovement
        from apps.inventory.tracking import TrackingMode

        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        if not (self.reason or "").strip():
            raise ValidationError("Say why the batches are being re-made.")
        if self.item.tracking != TrackingMode.LOT:
            raise ValidationError(f"{self.item} is not kept in batches that can be split "
                                  "or joined.")
        if self.warehouse.consignment_vendor_id:
            raise ValidationError(f"{self.warehouse} holds "
                                  f"{self.warehouse.consignment_vendor}'s stock.")
        from apps.core.models import UnitOfMeasureCategory

        counted = self.item.uom.category == UnitOfMeasureCategory.COUNT
        taken, made = list(self.taken()), list(self.made())
        if not taken or not made:
            raise ValidationError("A re-batch takes from at least one batch and makes at "
                                  "least one.")
        if len(taken) == 1 and len(made) == 1:
            raise ValidationError("One batch into one batch is the same batch.")
        lots = [line.lot for line in taken + made]
        if len({lot.pk for lot in lots}) != len(lots):
            raise ValidationError("Each batch appears once, on one side.")
        for line in taken + made:
            if line.lot.item_id != self.item_id:
                raise ValidationError(f"{line.lot} is not {self.item}.")
            if line.quantity is None or line.quantity <= 0:
                raise ValidationError(f"{line.lot}: a quantity is more than nothing.")
            if counted and line.quantity != line.quantity.to_integral_value():
                raise ValidationError(f"{line.lot}: {self.item} is counted whole.")
        total_in = sum((line.quantity for line in taken), ZERO)
        total_out = sum((line.quantity for line in made), ZERO)
        if total_in != total_out:
            raise ValidationError(f"{_q(total_in)} taken and {_q(total_out)} made: what goes "
                                  "in comes out.")
        lock_positions([(self.item, self.warehouse)])
        for line in taken:
            free = line.lot.on_hand_at(self.warehouse) - _baled(line.lot)
            if line.quantity > free:
                raise ValidationError(
                    f"{line.lot.code} has {_q(free)} on the shelf and not in a sealed bale; "
                    f"{_q(line.quantity)} were asked for."
                )
        for line in made:
            if line.lot.movements.exists():
                raise ValidationError(f"{line.lot.code} has held stock before; a re-batch "
                                      "makes new batches.")
        self.rebatched_on = to_date(self.rebatched_on)
        self.number = DocumentSequence.next_for("manufacturing.rebatch", self.rebatched_on,
                                                name="Re-batches", prefix="RB-")
        occurred_at, value = timezone.now(), ZERO
        notes = f"Re-batch {self.number}: {self.reason.strip()}"[:255]
        for line in taken:
            cost = self.item.cost_of_removing(self.warehouse, line.quantity, lot=line.lot)
            value += cost
            line.stock_movement = StockMovement.objects.create(
                item=self.item, warehouse=self.warehouse, lot=line.lot,
                movement_type=MovementType.TRANSFER_OUT, uom=self.item.uom,
                quantity=-line.quantity, unit_cost=(cost / line.quantity).quantize(
                    Decimal("0.000001")),
                reference=self.number, occurred_at=occurred_at, notes=notes)
            line._write(["stock_movement"])
        rate = (value / total_in).quantize(Decimal("0.000001"))
        expiries = [line.lot.expires_on for line in taken if line.lot.expires_on]
        for line in made:
            line.stock_movement = StockMovement.objects.create(
                item=self.item, warehouse=self.warehouse, lot=line.lot,
                movement_type=MovementType.TRANSFER_IN, uom=self.item.uom,
                quantity=line.quantity, unit_cost=rate,
                reference=self.number, occurred_at=occurred_at, notes=notes)
            line._write(["stock_movement"])
            if expiries and (line.lot.expires_on is None
                             or line.lot.expires_on > min(expiries)):
                # A fact of the goods: the soonest of what went into it.
                line.lot.expires_on = min(expiries)
                line.lot.save(update_fields=["expires_on", "updated_at"])
        self.reason = self.reason.strip()
        self.posted, self.posted_at = True, timezone.now()
        self._write(["number", "rebatched_on", "reason", "posted", "posted_at"])

    @transaction.atomic
    def void(self, reason):
        from apps.inventory.locking import lock_positions
        from apps.inventory.models import MovementType, StockMovement

        if not self.posted or self.voided_at is not None:
            raise ValidationError(f"{self} is not a standing re-batch.")
        if not (reason or "").strip():
            raise ValidationError("Say why the re-batch is withdrawn.")
        lock_positions([(self.item, self.warehouse)])
        made = list(self.made())
        for line in made:
            others = line.lot.movements.exclude(pk=line.stock_movement_id)
            if others.exists() or _baled(line.lot):
                raise ValidationError(f"{line.lot.code} has been used since it was made; "
                                      "the re-batch stands.")
        occurred_at = timezone.now()
        notes = f"Void of {self.number}: {reason.strip()}"[:255]
        for line in made + list(self.taken()):
            original = line.stock_movement
            StockMovement.objects.create(
                item=self.item, warehouse=self.warehouse, lot=line.lot,
                movement_type=(MovementType.TRANSFER_IN if original.quantity < 0
                               else MovementType.TRANSFER_OUT),
                uom=original.uom, quantity=-original.quantity,
                unit_cost=original.unit_cost, reference=self.number,
                occurred_at=occurred_at, notes=notes)
        self.voided_at, self.voided_reason = timezone.now(), reason.strip()
        self._write(["voided_at", "voided_reason"])


class RebatchSide(models.TextChoices):
    IN = "in", "Taken from"
    OUT = "out", "Made into"


class RebatchLine(AuditModel):
    rebatch = models.ForeignKey(Rebatch, on_delete=models.CASCADE, related_name="lines")
    side = models.CharField(max_length=3, choices=RebatchSide.choices)
    lot = models.ForeignKey("inventory.Lot", on_delete=models.PROTECT,
                            related_name="rebatch_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=4,
                                   help_text="In the item's stocking unit.")
    stock_movement = models.ForeignKey("inventory.StockMovement", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="+",
                                       editable=False)

    class Meta:
        ordering = ["rebatch", "side", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="rebatch_line_positive"),
        ]

    def __str__(self):
        return f"{self.get_side_display()} {self.lot}: {_q(self.quantity)}"

    def save(self, *args, **kwargs):
        if self.rebatch.posted and not getattr(self, "_writing", False):
            raise ValidationError(f"{self.rebatch} is posted; its lines do not change.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.rebatch.posted:
            raise ValidationError(f"{self.rebatch} is posted; its lines do not change.")
        return super().delete(*args, **kwargs)

    def _write(self, fields):
        self._writing = True
        try:
            super().save(update_fields=fields + ["updated_at"])
        finally:
            self._writing = False


def _baled(lot):
    from .bales import BaleLine, _standing

    return sum((line.quantity for line in _standing(BaleLine.objects.filter(lot=lot))), ZERO)


def _standing_lines(**filters):
    return RebatchLine.objects.filter(rebatch__posted=True, rebatch__voided_at__isnull=True,
                                      **filters)


def sources(lot):
    """The batches this one was re-made from, directly."""
    rebatches = _standing_lines(lot=lot, side=RebatchSide.OUT).values("rebatch")
    return [line.lot for line in _standing_lines(
        rebatch__in=rebatches, side=RebatchSide.IN).select_related("lot").order_by("id")]


def remade_into(lot):
    """[(re-batch, quantity taken from this batch, batches it made)], oldest first."""
    rows = []
    for line in _standing_lines(lot=lot, side=RebatchSide.IN).select_related(
            "rebatch").order_by("rebatch__rebatched_on", "rebatch_id"):
        rows.append((line.rebatch, line.quantity, [row.lot for row in line.rebatch.made()]))
    return rows


def rebatch(item, warehouse, taken, made, reason, on_date=None):
    """Take [(lot, quantity)] and make [(lot, quantity)], posted."""
    document = Rebatch.objects.create(item=item, warehouse=warehouse, reason=reason or "",
                                      rebatched_on=to_date(on_date) or timezone.localdate())
    for side, rows in ((RebatchSide.IN, taken), (RebatchSide.OUT, made)):
        for lot, quantity in rows:
            RebatchLine.objects.create(rebatch=document, side=side, lot=lot,
                                       quantity=Decimal(str(quantity)))
    document.post()
    return document

