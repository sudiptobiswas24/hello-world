"""
A starting point, so valuation does not begin at the beginning of time.

Stock value is replayed from the movement ledger every time anybody
asks, which is what keeps it honest: nothing is stored, so nothing can
drift from the movements that produced it. The cost is that the replay
is linear in movements, and it was measured rather than guessed at —
678 ms for a weighted average over twenty thousand movements, 815 ms
for FIFO. A delivery spanning three batches pays it three times. A
valuation report pays it once per item per warehouse.

A fast-moving line reaches twenty thousand movements in about two
years, so this is a wall rather than a worry. `costing.py` has said
since it was written that it "would want a periodic valuation snapshot
at much larger volumes" — an intention nothing implemented, which is
this codebase's own second defect shape.

A snapshot is not a second source of truth. It is a fold of the
movements up to a point, and it is thrown away the moment anything
lands at or before that point — a backdated receipt, a correction — so
the answer is always the replay's answer. What it changes is where the
replay starts:

    method   movements    unfolded     folded
    average       1000     26.2 ms     2.4 ms
    average      20000    563.4 ms     7.4 ms
    fifo          1000     29.3 ms     6.8 ms
    fifo         20000    649.7 ms    40.9 ms

The write path pays for it, which was measured too rather than waved
at: a movement insert went from 0.34 ms to 4.25 ms once every write
asked whether it was time to fold, and back to 1.28 ms once that
question was sampled instead (see FOLD_CHECK_EVERY). What remains is
one DELETE per write, and that one is not negotiable — it is what
keeps a stale fold from ever being read.
"""

from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone

from .models import Item, Warehouse

# How many movements may accumulate past a snapshot before a new one is
# folded. Low enough that the tail is cheap, high enough that folding is
# rare: at five hundred the worst replay is a fortieth of the twenty
# thousand measured above, and the fold itself is incremental.
SNAPSHOT_EVERY = 500

# Asking whether it is time to fold means counting what has landed since
# the last fold, and that count turned out to be the most expensive
# thing on the write path: 5.3 ms a movement, against 0.34 ms for the
# insert itself. So it is asked on a sample of writes rather than on all
# of them, and the movement's own id — uniform, and already in hand —
# decides which. What that costs is a fold landing at about five hundred
# movements instead of exactly five hundred, which costs nothing:
# SNAPSHOT_EVERY is a knob on how long the replayed tail is, not a
# number anything depends on. Correctness is not sampled — invalidate()
# runs on every single write.
FOLD_CHECK_EVERY = 64


class StockValuationSnapshot(models.Model):
    """
    What a shelf held and what it was worth as at one movement.

    Keyed by the method it was built under, because a FIFO fold means
    nothing to an averaged item and an item's method can change. A
    snapshot for a method the item no longer uses is simply not found.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="snapshots")
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.CASCADE,
        related_name="snapshots",
        help_text="Null for the valuation across every warehouse, which pools them "
                  "into one running average and is not the sum of the separate ones.",
    )
    method = models.CharField(max_length=16)
    boundary_at = models.DateTimeField(
        help_text="When the last movement folded into this happened. Anything "
                  "landing at or before it makes the fold wrong."
    )
    boundary_id = models.PositiveIntegerField(
        help_text="That movement's id, which breaks the tie when two share a moment."
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    value = models.DecimalField(max_digits=18, decimal_places=6)
    state = models.JSONField(
        default=dict,
        help_text="Whatever the method needs beyond a quantity and a value: FIFO's "
                  "layers, specific identification's per-batch pools. Empty for the "
                  "methods that need neither.",
    )
    folded_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # A null warehouse is distinct from every other null as far
            # as an index is concerned, so the two cases need separate
            # constraints.
            models.UniqueConstraint(
                fields=["item", "method"], condition=Q(warehouse__isnull=True),
                name="one_snapshot_per_item_and_method",
            ),
            models.UniqueConstraint(
                fields=["item", "warehouse", "method"],
                condition=Q(warehouse__isnull=False),
                name="one_snapshot_per_position_and_method",
            ),
        ]
        indexes = [models.Index(fields=["item", "warehouse", "method"])]

    def __str__(self):
        where = self.warehouse.code if self.warehouse_id else "everywhere"
        return f"{self.item.sku} @ {where} to {self.boundary_at}"


def usable_snapshot(item, warehouse, method, as_of=None):
    """
    The fold to start from, if there is one that is still true.

    A historical valuation may use it too, provided the fold ended
    before the date being asked about — the movements after it are
    simply replayed up to that date instead of up to now.
    """
    found = StockValuationSnapshot.objects.filter(item=item, method=method)
    found = (
        found.filter(warehouse__isnull=True) if warehouse is None
        else found.filter(warehouse=warehouse)
    )
    snapshot = found.first()
    if snapshot is None:
        return None
    if as_of is not None:
        # `as_of` is compared against occurred_at__date, which the database
        # evaluates in the active timezone; the boundary has to be read the
        # same way or the two disagree wherever that is not UTC, and the
        # fold would then quietly include a movement past the date asked
        # about.
        boundary = timezone.localtime(snapshot.boundary_at).date()
        if boundary > as_of:
            # The fold includes movements the caller has not asked about.
            return None
    return snapshot


def invalidate(item, warehouse, occurred_at):
    """
    Throw away any fold that a movement landing here would make wrong.

    Movements arrive backdated all the time — a receipt entered on
    Monday for Friday — and a fold that already counted past that point
    is answering a question about a ledger that no longer exists.
    """
    stale = StockValuationSnapshot.objects.filter(
        item=item, boundary_at__gte=occurred_at
    ).filter(Q(warehouse__isnull=True) | Q(warehouse=warehouse))
    stale.delete()


def fold(item, warehouse, method, quantity, value, state, boundary):
    """Record where the replay may start from next time."""
    if boundary is None:
        return None
    defaults = {
        "boundary_at": boundary.occurred_at,
        "boundary_id": boundary.pk,
        "quantity": quantity,
        "value": value,
        "state": state,
    }
    existing = StockValuationSnapshot.objects.filter(item=item, method=method)
    existing = (
        existing.filter(warehouse__isnull=True) if warehouse is None
        else existing.filter(warehouse=warehouse)
    )
    snapshot = existing.first()
    if snapshot is not None:
        for field, value_ in defaults.items():
            setattr(snapshot, field, value_)
        snapshot.save(update_fields=list(defaults) + ["folded_at"])
        return snapshot
    return StockValuationSnapshot.objects.create(
        item=item, warehouse=warehouse, method=method, **defaults
    )


def movements_past(item, warehouse, snapshot):
    """How many movements have landed since a fold."""
    from .models import StockMovement

    rows = StockMovement.objects.filter(item=item)
    if warehouse is not None:
        rows = rows.filter(warehouse=warehouse)
    if snapshot is not None:
        rows = rows.filter(
            Q(occurred_at__gt=snapshot.boundary_at)
            | Q(occurred_at=snapshot.boundary_at, id__gt=snapshot.boundary_id)
        )
    return rows.count()


def _fold_scope(item, scope):
    """Fold one view of an item — one shelf, or every shelf pooled."""
    from .costing import replay_with_state

    quantity, value, state, boundary = replay_with_state(item, scope)
    return fold(item, scope, item.costing_method, quantity, value, state, boundary)


def fold_position(item, warehouse):
    """
    Fold a starting point for this shelf and for the pooled view, now,
    however little has happened since the last one.

    The write path does not call this — it calls `maybe_fold`, which
    folds only once enough has accumulated to be worth it. This is for
    when somebody wants the folds to exist regardless: after a bulk
    import, or the first time this code is deployed onto a ledger that
    already has years of movements in it and may get no further writes.
    """
    for scope in (warehouse, None):
        _fold_scope(item, scope)


def maybe_fold(item, warehouse, movement_id=None):
    """
    Fold a new starting point once enough has happened since the last.

    Called from the write path rather than from a read, so a report
    never pays for somebody else's housekeeping and a rolled-back
    transaction never leaves a fold behind. The fold itself starts from
    the previous one, so it costs the tail and not the history.

    `movement_id` is the movement that prompted this, and most of the
    time it means "not this one" — see FOLD_CHECK_EVERY. Leaving it out
    asks the question properly, which is what the tests want and what
    anything deciding on its own schedule would want.
    """
    if movement_id is not None and movement_id % FOLD_CHECK_EVERY:
        return
    for scope in (warehouse, None):
        snapshot = usable_snapshot(item, scope, item.costing_method)
        if movements_past(item, scope, snapshot) >= SNAPSHOT_EVERY:
            _fold_scope(item, scope)
