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

import contextlib
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel, to_date

from .models import Item, StockMovement, Warehouse


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
        return self.expires_on < (to_date(on_date) or timezone.localdate())

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


# Why a batch may not be drawn whatever the shelf holds, said by the
# module that decides it (quality registers its release gate when it
# loads): `fn(item, lot, warehouse=...) -> words, or ""`. Here so a pick
# asks without inventory importing whoever answers.
LOT_HOLDS = []


def register_lot_hold(ask):
    if ask not in LOT_HOLDS:
        LOT_HOLDS.append(ask)


# One question to whoever answers LOT_HOLDS, across several asks (quality
# registers its per-question memory): a lot in five stores, or a page of
# lots, reads each batch's verdict and lineage once.
LOT_QUESTIONS = []


def register_lot_question(context):
    if context not in LOT_QUESTIONS:
        LOT_QUESTIONS.append(context)


@contextlib.contextmanager
def one_question():
    with contextlib.ExitStack() as stack:
        for context in LOT_QUESTIONS:
            stack.enter_context(context())
        yield


# What of a batch on a shelf is already promised to something that has not
# drawn it yet, asked of whoever knows (manufacturing: tape on a backflushed
# run's creels). Each answers `ask(lot, warehouse)` with a quantity in the
# stocking unit; inventory cannot import them.
LOT_COMMITMENTS = []


def register_lot_commitment(ask):
    if ask not in LOT_COMMITMENTS:
        LOT_COMMITMENTS.append(ask)


def committed_elsewhere(lot, warehouse):
    """How much of `lot` on `warehouse` something has already taken without drawing it."""
    return sum((ask(lot, warehouse) or Decimal("0") for ask in LOT_COMMITMENTS), Decimal("0"))


def lot_held(item, lot, warehouse, action="issue"):
    """
    Why this batch may not be drawn from `warehouse`, in words, or "".

    The warehouse is not optional. Asked of the batch alone, the gate
    could not see a quarantine bay: frames went to a subcontractor out of
    the inspection bay, and bundles there were baled (O147). `lot` is None
    for an item kept by no batch, which the bay holds just the same.
    """
    return next((said for said in (ask(item, lot, warehouse=warehouse, action=action)
                                   for ask in LOT_HOLDS) if said), "")


# Asked once for a page of batches by whoever answers LOT_HOLDS, before the
# page is answered batch by batch inside one question: `prefill(lots)` reads
# what each batch's answer needs for all of them at once (O231).
LOT_PAGE_QUESTIONS = []


def register_lot_page_question(prefill):
    if prefill not in LOT_PAGE_QUESTIONS:
        LOT_PAGE_QUESTIONS.append(prefill)


def held_because_page(lots):
    """
    {lot pk: held_because(lot)} for a page of batches, the page-wide form
    of the gate: where each is on hand in one query, each provider asked
    once for the page, then every batch answered in one question. Asked row
    by row, a page of 25 batches took 248 queries (O231).
    """
    lots = list(lots)
    if not lots:
        return {}
    places = {}
    rows = (StockMovement.objects.filter(lot__in=lots).values("lot_id", "warehouse_id")
            .annotate(held=Sum("quantity")).filter(held__gt=0).order_by("warehouse_id"))
    warehouses = Warehouse.objects.in_bulk({row["warehouse_id"] for row in rows})
    for row in rows:
        places.setdefault(row["lot_id"], []).append(warehouses[row["warehouse_id"]])
    with one_question():
        for prefill in LOT_PAGE_QUESTIONS:
            prefill(lots)
        return {lot.pk: _said_from(lot, places.get(lot.pk, [])) for lot in lots}


def _said_from(lot, places):
    said = []
    for warehouse in places:
        words = lot_held(lot.item, lot, warehouse, action="go")
        if words and words not in said:
            said.append(words)
    return " ".join(said)


def held_because(lot):
    """
    Why this batch may not go, in the gate's own words, from each place it
    is on hand: "" for one that may go from all of them. What a stock list
    shows beside the batch (O207): held lots stay listed, and say why.
    Worked out from the gate when asked, never stored.
    """
    # One query for where it is on hand, not one for the balances and another for the places.
    places = (Warehouse.objects.filter(movements__lot=lot).annotate(held=Sum("movements__quantity"))
              .filter(held__gt=0).order_by("pk"))
    with one_question():
        return _said_from(lot, places)


def allocate(item, warehouse, quantity, on_date=None, allow_expired=False, holds_let_through=None,
             preferring=None):
    """
    Choose which lots to ship, soonest to expire first.

    First-expired-first-out rather than first-in-first-out: the goods
    that will be worthless next week should leave before the goods that
    keep for a year, whatever order they arrived in. For stock with no
    expiry the two rules coincide, since lots are then taken in the
    order they were created.

    Expired stock is skipped rather than silently included. Shipping it
    is the one outcome a tracked item exists to prevent. So is a batch
    quality holds: chosen for nobody in particular, a rejected drum went
    onto a lorry once its inspection plan was retired, because the line
    that named no batch was the line nothing asked about.

    `holds_let_through(item, lot)` names the holds a caller takes anyway:
    a subcontractor's assemblies consume the batch they were made from
    whatever quality said of it after it was sent (owner's decision, item
    22). Everything else the gate says still skips the batch.

    `preferring` ({lot pk: quantity}) is taken first, up to those
    quantities, soonest to expire first; then anything else, as above. A
    subcontractor's receipt prefers what was sent on its own order: first
    expired over the whole bay, order B's receipt consumed the batch sent
    for order A, which quality had rejected, and B's assemblies were held
    while A's, made from it, were not.
    """
    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValidationError("Nothing to allocate.")
    on_date = to_date(on_date) or timezone.localdate()

    taken = []
    remaining = quantity
    skipped = Decimal("0")
    held = Decimal("0")
    why = ""
    # Less what is already promised and not yet drawn: a doff on a
    # backflushed run's creels stays on the shelf until the output draws
    # it, and a pick naming no batch chose it all the same (O133).
    found, promised = [], Decimal("0")
    for lot, available in lots_at(item, warehouse):
        on_creels = min(committed_elsewhere(lot, warehouse), available)
        promised += on_creels
        if available - on_creels > 0:
            found.append((lot, available - on_creels))
    preferring = {pk: Decimal(share) for pk, share in (preferring or {}).items() if share > 0}
    first = [(lot, min(available, preferring[lot.pk])) for lot, available in found if lot.pk in preferring]
    shares = {lot.pk: share for lot, share in first}
    then = [(lot, available - shares.get(lot.pk, Decimal("0"))) for lot, available in found]
    chosen = {}
    for lot, available in first + [pair for pair in then if pair[1] > 0]:
        if remaining <= 0:
            break
        if not allow_expired and lot.has_expired(on_date):
            skipped += available
            continue
        said = lot_held(item, lot, warehouse)
        if said and not (holds_let_through and holds_let_through(item, lot)):
            held += available
            why = why or said
            continue
        drawn = min(available, remaining)
        if lot.pk not in chosen:
            chosen[lot.pk] = [lot, Decimal("0")]
            taken.append(chosen[lot.pk])
        chosen[lot.pk][1] += drawn
        remaining -= drawn
    taken = [(lot, drawn) for lot, drawn in taken]

    if remaining > 0:
        # As figures, not at the column's scale: PostgreSQL sums to four
        # places, and the message said "Only 300.0000" (O134).
        shown = lambda value: format(value.normalize(), "f")  # noqa: E731
        message = (
            f"Only {shown(quantity - remaining)} of {item} can be allocated at {warehouse}; "
            f"{shown(remaining)} short."
        )
        if skipped:
            message += f" {shown(skipped)} is on the shelf but expired."
        if held:
            message += f" {shown(held)} is on the shelf and held by quality: {why}"
        if promised:
            message += (f" {shown(promised)} is on the shelf and loaded on a run "
                        "that has not drawn it yet.")
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
