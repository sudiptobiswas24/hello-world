"""
Lots, serial numbers and expiry.

Until now a widget was a widget. Which batch it came from, when it goes
off, and which specific unit went to which customer were questions this
system could not answer — and they are the questions asked when
something goes wrong, by a regulator, an auditor, or a customer holding
a recalled part.

Scope, stated rather than implied: tracking here is about quantity and
traceability, not costing. Stock stays valued at weighted average per
item and warehouse. Per-lot specific-identification costing is a
separate and much larger change, and smuggling it in alongside would
make two changes look like one.

A serial number is a lot that can only ever be one unit, which is how
it behaves everywhere that matters. Keeping them one model means the
allocation, expiry and traceability code is written once instead of
twice with the second copy drifting.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel, to_date

from .models import Item, Warehouse


class TrackingMode(models.TextChoices):
    NONE = "none", "Not tracked"
    LOT = "lot", "By lot or batch"
    SERIAL = "serial", "By serial number"


class Lot(AuditModel):
    """
    A batch of an item, or — when the item is serial-tracked — a single
    unit of it.

    The expiry date lives here rather than on the movement because it is
    a property of the goods, not of the journey they made. A lot that
    goes off on the first of June goes off on the first of June whether
    it moved once or ten times.
    """

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="lots")
    code = models.CharField(max_length=64, help_text="Batch number, or the serial number.")
    expires_on = models.DateField(
        null=True, blank=True,
        help_text="After this date the goods may not be shipped. Left blank they "
                  "do not expire.",
    )
    manufactured_on = models.DateField(null=True, blank=True)
    supplier_reference = models.CharField(
        max_length=64, blank=True,
        help_text="The vendor's own batch number, kept so a recall notice quoting "
                  "it can be matched to stock here.",
    )
    notes = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["item", "expires_on", "code"]
        constraints = [
            models.UniqueConstraint(fields=["item", "code"], name="one_lot_code_per_item"),
        ]
        indexes = [models.Index(fields=["item", "expires_on"])]

    def __str__(self):
        return f"{self.item.sku} / {self.code}"

    def is_serial(self):
        return self.item.tracking == TrackingMode.SERIAL

    def has_expired(self, on_date=None):
        if self.expires_on is None:
            return False
        return self.expires_on < (to_date(on_date) or timezone.now().date())

    def on_hand_at(self, warehouse=None):
        movements = self.movements.all()
        if warehouse is not None:
            movements = movements.filter(warehouse=warehouse)
        return movements.aggregate(total=Sum("quantity"))["total"] or Decimal("0")

    def warehouses(self):
        """Where this lot currently is, and how much of it is there."""
        rows = (
            self.movements.values("warehouse")
            .annotate(quantity=Sum("quantity"))
            .filter(quantity__gt=0)
        )
        by_pk = {row["warehouse"]: row["quantity"] for row in rows}
        return [
            (warehouse, by_pk[warehouse.pk])
            for warehouse in Warehouse.objects.filter(pk__in=by_pk)
        ]


def lots_at(item, warehouse, include_empty=False, on_date=None):
    """
    What is on this shelf, lot by lot, soonest to expire first.

    Sorted that way because that is the order stock should leave in. A
    lot with no expiry sorts last: it will keep, so it can wait.
    """
    rows = (
        Lot.objects.filter(item=item, movements__warehouse=warehouse)
        .annotate(quantity=Sum("movements__quantity", filter=Q(movements__warehouse=warehouse)))
        .distinct()
    )
    found = [
        (lot, lot.quantity or Decimal("0"))
        for lot in rows
        if include_empty or (lot.quantity or Decimal("0")) > 0
    ]
    return sorted(found, key=lambda pair: _expiry_order(pair[0]))


def _expiry_order(lot):
    """Soonest to expire first; no expiry sorts last, since it can wait."""
    return (lot.expires_on is None, lot.expires_on or datetime.date.max, lot.pk)


def allocate(item, warehouse, quantity, on_date=None, allow_expired=False):
    """
    Choose which lots to ship, soonest to expire first.

    First-expired-first-out rather than first-in-first-out: the goods
    that will be worthless next week should leave before the goods that
    keep for a year, whatever order they arrived in. For stock with no
    expiry the two rules coincide, since lots are then taken in the
    order they were created.

    Expired stock is skipped rather than silently included. Shipping it
    is the one outcome a tracked item exists to prevent.
    """
    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValidationError("Nothing to allocate.")
    on_date = to_date(on_date) or timezone.now().date()

    taken = []
    remaining = quantity
    skipped = Decimal("0")
    for lot, available in lots_at(item, warehouse):
        if remaining <= 0:
            break
        if not allow_expired and lot.has_expired(on_date):
            skipped += available
            continue
        drawn = min(available, remaining)
        taken.append((lot, drawn))
        remaining -= drawn

    if remaining > 0:
        message = (
            f"Only {quantity - remaining} of {item} can be allocated at {warehouse}; "
            f"{remaining} short."
        )
        if skipped:
            message += f" {skipped} is on the shelf but expired."
        raise ValidationError(message)
    return taken


def expiring(before, warehouse=None, item=None):
    """
    Lots that go off before `before` and still have stock on a shelf.

    Reported rather than acted on. What to do about stock about to
    expire — discount it, move it, write it off — is a decision, and a
    system that quietly wrote it off would be making that decision for
    somebody.
    """
    before = to_date(before)
    lots = Lot.objects.filter(expires_on__isnull=False, expires_on__lt=before)
    if item is not None:
        lots = lots.filter(item=item)

    rows = []
    for lot in lots.select_related("item"):
        held = lot.on_hand_at(warehouse)
        if held > 0:
            rows.append({
                "lot": lot,
                "item": lot.item,
                "quantity": held,
                "expires_on": lot.expires_on,
                "expired": lot.has_expired(),
            })
    return sorted(rows, key=lambda row: row["expires_on"])


def traceability(lot):
    """
    Everywhere a lot has been, in order.

    The question a recall asks. It is answered by reading the movement
    ledger rather than by a separate trail, for the same reason on-hand
    is: a second record of where stock went is a record that can
    disagree with the first.
    """
    return [
        {
            "movement": movement,
            "warehouse": movement.warehouse,
            "quantity": movement.quantity,
            "type": movement.movement_type,
            "reference": movement.reference,
            "occurred_at": movement.occurred_at,
        }
        for movement in lot.movements.order_by("occurred_at", "id").select_related("warehouse")
    ]
