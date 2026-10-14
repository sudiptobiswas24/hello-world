"""
A recall (RISKS.md, "Decisions taken", 23; O207).

A batch rejected, held for rework or left awaiting the Quality Manager
after lots were made from it holds what was made from it: the gate reads
that from the lineage (`release.recalled_from`), never from a flag, so the
source's verdict voided or passed again hands each lot back to what it
read before. This is the telling, and the decision: each lot made from the
batch that the verdict holds gets a recall, an inspection that measures
nothing and waits for the Quality Manager (`Inspection.decide()`: use as
is, rework, reject), with one to-do naming the lot, where it is, and what
of it has already gone, by document, so they can call it back.

Told once a lot. A lot with a recall still open is not told again, and the
lots are locked before that is asked (`Inspection.post`), so two
rejections at once on two sources of one lot make one to-do, not two.

Nothing shipped or consumed is touched: the movements that took it stand.
"""

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.models import DocumentSequence

from .models import Disposition, Inspection, ReleaseStatus
from .release import (
    RELEASING,
    latest_inspection,
    lots_made_from,
    open_recall,
    recall_plan,
    release_status,
    verdict_words,
)

ZERO = Decimal("0")


def _figure(quantity):
    return format(quantity.normalize(), "f") if quantity else "0"


def where_it_is(lot):
    """([(warehouse, quantity)] still in stock, [(reference, quantity)] gone), from the movement ledger."""
    from apps.inventory.models import MovementType

    held, net = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    names = {}
    moves = lot.movements.select_related("warehouse").order_by("occurred_at", "id")
    for movement in moves:
        names[movement.warehouse_id] = movement.warehouse
        held[movement.warehouse_id] += movement.quantity
        if movement.movement_type not in (MovementType.TRANSFER_IN, MovementType.TRANSFER_OUT):
            net[movement.reference or "no document"] += movement.quantity
    in_stock = [(names[pk], quantity) for pk, quantity in held.items() if quantity > 0]
    gone = [(reference, -quantity) for reference, quantity in net.items() if quantity < 0]
    return in_stock, gone


def _body(lot, source, verdict, own, recall_number):
    unit = lot.item.uom.code if lot.item.uom_id else ""
    in_stock, gone = where_it_is(lot)
    said = [f"{lot} ({lot.item}) is held: it is made from {source}, {verdict_words(verdict)}."]
    if own is not None:
        said.append(f"Its own release ({own.number}, {own.inspected_on:%d %b %Y}) came before that.")
    said.append("In stock: " + ", ".join(f"{_figure(quantity)} {unit} in {warehouse}"
                                         for warehouse, quantity in in_stock) + "."
                if in_stock else "Nothing of it is in stock.")
    said.append("Already gone, not touched: " + ", ".join(f"{_figure(quantity)} {unit} on {reference}"
                                                          for reference, quantity in gone) + "."
                if gone else "Nothing of it has gone.")
    said.append(f"Decide {recall_number}: use it as is, rework it or reject it. Until then nobody "
                "may issue it, load it or ship it.")
    return " ".join(said)


def recall(verdict, by=None):
    """
    What `verdict` (just posted, holding its batch) recalls: a recall
    awaiting the Quality Manager for each lot made from the batch that it
    holds now, with a to-do each. Returns [recall]. Called by
    Inspection.post() with the batch and every lot made from it locked.

    A lot held by its own verdict already, or with a recall open, is not
    recalled again. A lot with no plan to file a recall under (no
    inspection of its own and none in force) is held all the same, by the
    gate; it is not told.
    """
    from .decisions import tell_the_quality_manager

    source, made = verdict.lot, []
    for lot in lots_made_from(verdict.lot):
        if release_status(lot) != ReleaseStatus.HELD or open_recall(lot) is not None:
            continue
        own = latest_inspection(lot)
        if own is not None and own.disposition not in RELEASING:
            continue
        plan = recall_plan(lot)
        if plan is None:
            continue
        today = timezone.localdate()
        recall_of = Inspection(
            lot=lot, plan=plan, inspected_on=today, disposition=Disposition.AWAITING,
            recalled_by=verdict, posted=True, posted_at=timezone.now(),
            number=DocumentSequence.next_for("quality.inspection", today, name="Inspections",
                                             prefix="QC-"),
        )
        recall_of.notes = _body(lot, source, verdict, own, recall_of.number)
        recall_of.save()
        told = tell_the_quality_manager(
            recall_of, f"Decide {recall_of.number}: {lot} recalled, made from {source} ({verdict.number})",
            link=f"/quality/inspections/{recall_of.pk}", by=by, body=recall_of.notes)
        said = [f"{lot} is recalled ({recall_of.number})."]
        if told.told:
            said.append(f"Told to decide: {', '.join(told.told)}.")
        said.extend(told.failures())
        Inspection.objects.filter(pk=recall_of.pk).update(told=" ".join(said))
        made.append(recall_of)
    return made


def recall_stands(recall_of):
    """Whether what recalled this lot holds it still: the source's verdict not voided, nor passed again."""
    return release_status(recall_of.lot) == ReleaseStatus.HELD


def lift_when_committed(lot, why):
    """
    Once this commits, close each open recall of what was made from `lot`
    that nothing holds any more: voided, its to-dos closed, `why` said.

    After the commit and in its own transaction, never inside the verdict's:
    the recall is a row of the same table, mostly older than a
    re-inspection of its source, and taken after it would be taken out of
    key order. The hold itself does not wait for this (the gate reads it
    from the source's verdict); only the recall's to-do does.
    """
    def lift():
        with transaction.atomic():
            ids = [each.pk for each in lots_made_from(lot)]
            for recall_of in Inspection.objects.filter(
                    lot_id__in=ids, posted=True, voided_at__isnull=True, recalled_by__isnull=False,
                    disposition=Disposition.AWAITING).order_by("pk"):
                if not recall_stands(recall_of):
                    recall_of.void(f"The recall is lifted: {why}"[:255])

    transaction.on_commit(lift)

