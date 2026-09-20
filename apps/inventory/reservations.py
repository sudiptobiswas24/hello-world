"""
Stock promised to a document but not yet shipped.

`available_at()` returned what was on the shelf. Two confirmed orders
for twenty-five each could both be taken against a shelf holding thirty,
and nothing said so until the second picker went looking. On-hand is not
availability: a unit already promised to somebody is not available to
anybody else.

A reservation is deliberately not a movement. The stock has not gone
anywhere, it is still owned and still valued, and writing it out of one
warehouse and into a "reserved" one would make the valuation ledger tell
a story about intent rather than about goods. It is a claim against a
quantity, held beside the ledger, and the ledger stays a record of what
physically moved.

The pointer is generic because inventory may not import sales. The
document that would be meaningless without the stock is the one that
knows about the reservation, and inventory only has to know that
something claimed a quantity — not what.
"""

from decimal import Decimal

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel

from .models import Item, Warehouse


class ReservationQuerySet(models.QuerySet):
    """
    Both filters chain, in either order. A manager method that returned a
    plain queryset would let `for_source(...)` be written and
    `.open()` not exist on the result, which is how a released claim gets
    counted as still held.
    """

    def open(self):
        return self.filter(released_at__isnull=True)

    def for_source(self, source):
        return self.filter(
            content_type=ContentType.objects.get_for_model(source), object_id=source.pk
        )


class ReservationManager(models.Manager.from_queryset(ReservationQuerySet)):
    @transaction.atomic
    def claim(self, source, item, warehouse, quantity):
        """
        Reserve what can be reserved, and report honestly on the rest.

        Refusing an order because the stock is not on the shelf would be
        wrong: orders are taken precisely so the stock can be bought.
        What is wrong is pretending the order is covered. So the claim
        takes what is free and returns the reservation and the shortfall,
        and the caller decides whether a shortfall matters.
        """
        if not item.track_inventory:
            return None, Decimal("0")
        quantity = Decimal(quantity)
        if quantity <= 0:
            raise ValidationError("A reservation must claim a positive quantity.")

        existing = self.for_source(source).open().first()
        already = existing.remaining() if existing else Decimal("0")
        free = Decimal(item.available_at(warehouse)) + already
        taken = min(quantity, max(free, Decimal("0")))
        shortfall = quantity - taken

        if taken <= 0:
            if existing:
                existing.release("Nothing free to hold")
            return None, shortfall

        if existing:
            if taken < existing.consumed:
                raise ValidationError(
                    f"{existing.consumed} of this claim has already shipped; it cannot "
                    f"be reduced to {taken}."
                )
            existing.quantity = taken
            existing.warehouse = warehouse
            super(StockReservation, existing).save(
                update_fields=["quantity", "warehouse", "updated_at"]
            )
            return existing, shortfall

        return self.create(
            source=source, item=item, warehouse=warehouse, quantity=taken
        ), shortfall


class StockReservation(AuditModel):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="reservations")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="reservations"
    )
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="Claimed, in the item's stocking unit.",
    )
    consumed = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("0"), editable=False,
        help_text="How much of the claim has actually shipped. Kept beside the "
                  "claim rather than subtracted from it, so the record still says "
                  "what was promised as well as what is left.",
    )
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="+", editable=False
    )
    object_id = models.PositiveIntegerField(editable=False)
    source = GenericForeignKey("content_type", "object_id")
    released_at = models.DateTimeField(null=True, blank=True, editable=False)
    released_reason = models.CharField(max_length=255, blank=True, editable=False)

    objects = ReservationManager()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="reservation_quantity_positive"
            ),
            models.UniqueConstraint(
                fields=["content_type", "object_id"],
                condition=Q(released_at__isnull=True),
                name="one_open_reservation_per_source",
            ),
        ]
        indexes = [models.Index(fields=["item", "warehouse", "released_at"])]

    def __str__(self):
        return f"{self.quantity} {self.item.sku} held at {self.warehouse.code}"

    def is_open(self):
        return self.released_at is None

    def remaining(self):
        """Still promised and not yet shipped."""
        if not self.is_open():
            return Decimal("0")
        return self.quantity - self.consumed

    @transaction.atomic
    def consume(self, quantity):
        """
        Draw the reservation down as the goods actually leave.

        The shipment that consumes a reservation is the same stock the
        reservation was holding, so it must not be checked against
        availability a second time — that is the double-count that makes
        an order unable to ship the units it reserved.
        """
        quantity = Decimal(quantity)
        if not self.is_open():
            raise ValidationError("This reservation has already been released.")
        drawn = min(quantity, self.remaining())
        if drawn <= 0:
            return Decimal("0")
        self.consumed += drawn
        fields = ["consumed", "updated_at"]
        if self.remaining() <= 0:
            self.released_at = timezone.now()
            self.released_reason = "Shipped"
            fields += ["released_at", "released_reason"]
        super().save(update_fields=fields)
        return drawn

    @transaction.atomic
    def release(self, reason=""):
        """Give the claim back. Cancelling an order frees its stock."""
        if not self.is_open():
            return self
        self.released_at = timezone.now()
        self.released_reason = reason or "Released"
        super().save(update_fields=["released_at", "released_reason", "updated_at"])
        return self

    def save(self, *args, **kwargs):
        if self.pk and StockReservation.objects.filter(
            pk=self.pk, released_at__isnull=False
        ).exists():
            raise ValidationError(
                "A released reservation is finished. Claim a new one instead."
            )
        if self.item_id and not self.item.track_inventory:
            raise ValidationError(f"{self.item} is not stocked, so nothing can be held.")
        super().save(*args, **kwargs)


def reserved_at(item, warehouse):
    """How much of `item` at `warehouse` is already promised to somebody."""
    total = StockReservation.objects.open().filter(
        item=item, warehouse=warehouse
    ).aggregate(total=Sum(F("quantity") - F("consumed")))["total"]
    return total or Decimal("0")


def release_for(source, reason=""):
    """Free everything a document was holding."""
    released = []
    for reservation in StockReservation.objects.for_source(source).open():
        released.append(reservation.release(reason))
    return released
