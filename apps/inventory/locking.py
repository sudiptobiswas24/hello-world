"""
Serialising the things that read a position and then change it.

Every posting path in this system has the same shape: read how much is
on the shelf, decide whether the document may go ahead, write the
movements. Between the read and the write there is nothing stopping
another transaction doing exactly the same thing, so two shipments of
the last ten units both find ten, both pass, and both post. The shelf
ends at minus ten and neither document did anything wrong.

The whole codebase had one `select_for_update`, on the document
sequence. Nothing else took a lock, and no test could ever have found
it: a test suite is single-threaded, so the window never opens.

On-hand quantity is derived from the movement ledger and there is no
row that represents a position, so there is nothing to lock. This
module supplies one. It holds no quantity and no value — a stored
position would drift from the ledger the moment anything was corrected,
and this codebase derives everything for exactly that reason. The row
exists to be locked and says nothing.

Postgres gives a real row lock. SQLite ignores `select_for_update` and
serialises writers with a database-wide lock instead, so development
behaves safely by accident rather than by this mechanism — which is
worth knowing before concluding from a green suite that the locking
works.
"""

from django.db import IntegrityError, models, transaction

from .models import Item, Warehouse


class StockPosition(models.Model):
    """
    One row per item and warehouse, to be locked and nothing else.

    Deliberately not an AuditModel and deliberately holding no numbers:
    it is a mutex, and a mutex that also claims to know how much stock
    there is would be a second answer to a question the ledger already
    answers.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="positions")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE, related_name="positions"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "warehouse"], name="one_position_per_item_and_warehouse"
            ),
        ]

    def __str__(self):
        return f"{self.item.sku} @ {self.warehouse.code}"


def lock_position(item, warehouse):
    """
    Hold the position for this item and warehouse until the transaction
    ends.

    Call it before reading how much is on the shelf, not before writing
    the movement: the race is between the read and the write, and a lock
    taken after the decision has already been made protects nothing.

    Taking it twice in one transaction is free — a row lock is held by
    the transaction, not by the caller — so a path that locks early and
    then posts through something that locks again pays once.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "lock_position() outside a transaction locks nothing; the lock is "
            "released the moment it is taken. Wrap the posting path in "
            "transaction.atomic()."
        )
    if item is None or warehouse is None:
        # A valuation across every warehouse has no single position to
        # hold. Callers that need one name the warehouse.
        return None
    # One query once the row exists, which after the first posting for a
    # pair is every time. get_or_create() first would spend a SELECT and
    # sometimes an INSERT on every posting for the life of the system.
    held = (
        StockPosition.objects.select_for_update()
        .filter(item=item, warehouse=warehouse)
        .first()
    )
    if held is not None:
        return held
    try:
        with transaction.atomic():
            StockPosition.objects.create(item=item, warehouse=warehouse)
    except IntegrityError:
        # Another transaction created it between the look and the write.
        # Theirs is as good as ours; what matters is that one exists to
        # lock.
        pass
    return (
        StockPosition.objects.select_for_update()
        .filter(item=item, warehouse=warehouse)
        .first()
    )


def lock_positions(pairs):
    """
    Hold several positions, in a fixed order.

    Ordered by primary key so two documents touching the same pair of
    items take them in the same sequence. Taking them in the order they
    happen to appear on a document is how two transfers between the same
    two warehouses deadlock against each other.
    """
    held = []
    for item, warehouse in sorted(
        {(i, w) for i, w in pairs if i is not None and w is not None},
        key=lambda pair: (pair[0].pk, pair[1].pk),
    ):
        held.append(lock_position(item, warehouse))
    return held
