"""
Where in the building the stock actually is.

A warehouse was a single undivided space. That is fine for the ledger —
value is held per item and warehouse, and a bin does not change what
anything is worth — and useless to the person holding a pick list in a
building with forty aisles.

Bins are therefore a picking concern, not an accounting one, and this
module keeps that line. Valuation is not reported per bin and is not
replayed per bin; asking for it would push a four-way product of item,
warehouse, batch and bin through a replay that is already linear in
movements, to answer a question nobody asks. What a bin answers is
where to walk.

The hierarchy is one model pointing at itself — aisle, rack, shelf —
because a warehouse that starts with twelve bins acquires zones later,
and the alternative is a second model the day it does.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from apps.core.models import AuditModel

from .models import Item, Warehouse


class StorageBin(AuditModel):
    """
    A named place inside a warehouse.

    `is_pickable` is the leaf test. An aisle is a place you can describe
    and not a place you can put a pallet; putting stock in one means
    nobody can be told where to find it beyond "somewhere down there".
    """

    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="bins")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children",
        help_text="The larger place this one sits in — a rack within an aisle.",
    )
    code = models.CharField(max_length=32, help_text="e.g. A-04-3")
    name = models.CharField(max_length=255, blank=True)
    sequence = models.PositiveIntegerField(
        default=0,
        help_text="Walking order. A pick list sorted by this is a route rather "
                  "than a list of places in no particular order.",
    )
    is_pickable = models.BooleanField(
        default=True,
        help_text="False for a grouping level — an aisle or a zone — that names "
                  "somewhere without being somewhere stock can sit.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["warehouse", "sequence", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "code"], name="one_bin_code_per_warehouse"
            ),
        ]
        indexes = [models.Index(fields=["warehouse", "is_pickable"])]

    def __str__(self):
        return f"{self.warehouse.code}/{self.code}"

    def clean(self):
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError("A bin cannot sit inside itself.")
        if self.parent_id and self.parent.warehouse_id != self.warehouse_id:
            raise ValidationError(
                f"{self.parent} is in another warehouse; a bin cannot sit inside it."
            )

    def path(self):
        """Aisle to shelf, outermost first."""
        places, seen = [], set()
        node = self
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            places.append(node)
            node = node.parent
        return list(reversed(places))

    def descendants(self):
        """This bin and everything inside it."""
        found, frontier = [self], [self]
        seen = {self.pk}
        while frontier:
            children = StorageBin.objects.filter(parent__in=frontier).exclude(pk__in=seen)
            children = list(children)
            if not children:
                break
            for child in children:
                seen.add(child.pk)
            found.extend(children)
            frontier = children
        return found

    def on_hand(self, item, include_children=True):
        """
        How much of `item` is here.

        A grouping level holds nothing itself, so it answers for
        everything beneath it — which is the only reading of "how much is
        in aisle A" that anybody means.
        """
        bins = self.descendants() if include_children else [self]
        total = item.movements.filter(bin__in=bins).aggregate(
            total=Sum("quantity")
        )["total"]
        return total or Decimal("0")

    def save(self, *args, **kwargs):
        # Django does not call full_clean() on save, and these bins are
        # built in code as often as in a form, so the check has to be here
        # or it is not a check.
        self.clean()
        super().save(*args, **kwargs)


def bins_holding(item, warehouse, lot=None):
    """
    Which bins hold this item, in walking order.

    The order is the point: a pick list is a route, and sorting by bin
    code alphabetically sends somebody from A-01 to A-10 to A-02.
    """
    movements = item.movements.filter(warehouse=warehouse, bin__isnull=False)
    if lot is not None:
        movements = movements.filter(lot=lot)
    rows = movements.values("bin").annotate(quantity=Sum("quantity")).filter(quantity__gt=0)
    by_pk = {row["bin"]: row["quantity"] for row in rows}
    found = StorageBin.objects.filter(pk__in=by_pk).order_by("sequence", "code")
    return [(storage_bin, by_pk[storage_bin.pk]) for storage_bin in found]


def unbinned(item, warehouse):
    """
    Stock on the shelf that nobody has said where to find.

    Reported rather than hidden: a warehouse that requires bins will
    refuse new movements without one, but anything booked in before that
    rule was turned on is still here and still findable only by looking.
    """
    total = item.movements.filter(warehouse=warehouse, bin__isnull=True).aggregate(
        total=Sum("quantity")
    )["total"]
    return total or Decimal("0")


def suggest_pick(item, warehouse, quantity, lot=None):
    """
    Where to go, and how much to take from each, in walking order.

    Fullest bin first within the route would minimise stops; walking
    order minimises walking. This picks walking order because the person
    doing it is crossing the building either way, and a route that
    doubles back is the complaint that gets made.
    """
    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValidationError("Nothing to pick.")
    plan, remaining = [], quantity
    for storage_bin, available in bins_holding(item, warehouse, lot=lot):
        if remaining <= 0:
            break
        drawn = min(available, remaining)
        plan.append((storage_bin, drawn))
        remaining -= drawn
    if remaining > 0:
        loose = unbinned(item, warehouse)
        message = (
            f"Only {quantity - remaining} of {item} is in a bin at {warehouse}; "
            f"{remaining} short."
        )
        if loose > 0:
            message += f" {loose} is on the shelf with no bin recorded."
        raise ValidationError(message)
    return plan


def suggest_putaway(item, warehouse, prefer_occupied=True):
    """
    Where to put goods that have just arrived.

    Next to the same item by default, because a warehouse in which one
    SKU lives in six places is a warehouse in which picking it is six
    walks. Falls back to the first empty pickable bin in walking order.
    """
    if prefer_occupied:
        occupied = bins_holding(item, warehouse)
        for storage_bin, _quantity in occupied:
            if storage_bin.is_pickable and storage_bin.is_active:
                return storage_bin
    for storage_bin in StorageBin.objects.filter(
        warehouse=warehouse, is_pickable=True, is_active=True
    ).order_by("sequence", "code"):
        return storage_bin
    return None
