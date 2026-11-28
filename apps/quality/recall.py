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

from apps.core.models import DocumentSequence, lock_rows

from .models import Disposition, Inspection, ReleaseStatus
from .release import (
    HOLDING,
    RELEASING,
    holding_sources,
    lot_and_sources,
    latest_inspection,
    lots_made_from,
    one_question,
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

    Followed as far as the hold reaches: the hold is read from the lineage
    with no limit, so the telling has none either (review F6), and the walk
    ends on a loop because it visits each batch once.

    A lot with a recall already open is told again, on that recall, naming
    this hold too (review F4): the Quality Manager decides it knowing every
    batch inside it that is held. A lot awaiting its own decision gets a
    recall too, so that taking its own failure as is does not take what is
    inside it as is. A lot rejected or held for rework by its own verdict is
    held whatever is inside it, and not recalled. A lot with no inspection
    plan to file a recall under is named in a to-do on the verdict itself.
    """
    from .decisions import tell_the_quality_manager

    source, made, unfiled = verdict.lot, [], []
    with one_question():
        for lot in lots_made_from(verdict.lot):
            waiting = open_recall(lot)
            if waiting is not None:
                also_held(waiting, verdict, by)
                continue
            if release_status(lot) != ReleaseStatus.HELD:
                continue
            own = latest_inspection(lot)
            if own is not None and own.disposition in (Disposition.REJECT, Disposition.REWORK):
                continue
            plan = recall_plan(lot)
            if plan is None:
                unfiled.append(lot)
                continue
            made.append(_recall_one(verdict, lot, own, plan, by))
    if unfiled:
        said = [f"{source} is {verdict_words(verdict)}.",
                "Held with it, made from it, and not given a recall to decide because the item has "
                "no inspection plan to file one under (it goes again when the source does, or once "
                "it is inspected under a plan): " + "; ".join(_where(lot) for lot in unfiled) + "."]
        tell_the_quality_manager(
            verdict, f"Recall from {source} ({verdict.number}): lots without a recall to decide",
            link=f"/quality/inspections/{verdict.pk}", by=by, body=" ".join(said))
    return made


def also_held(recall_of, verdict, by=None):
    """
    A further hold on a lot whose recall is still open (review F4): the
    Quality Manager's open to-do on the recall now names it too, so one
    to-do a lot still says every batch inside it that holds it (two
    rejections at once on two sources of one lot: one to-do, naming both).
    With no to-do open on it any more, they are given a new one. A note on
    the recall records it either way; the posted recall is not rewritten.
    """
    from django.contrib.contenttypes.models import ContentType

    from apps.core.chatter import FollowUp, Note

    from .decisions import tell_the_quality_manager

    line = (f"Also held: {recall_of.lot} is made from {verdict.lot}, {verdict_words(verdict)}. "
            f"Decide {recall_of.number} knowing both.")
    kind = ContentType.objects.get_for_model(Inspection)
    Note.objects.create(content_type=kind, object_id=recall_of.pk, body=line, created_by=by, updated_by=by)
    waiting = list(FollowUp.objects.filter(content_type=kind, object_id=recall_of.pk,
                                           done_on__isnull=True).order_by("pk"))
    for to_do in waiting:
        FollowUp.objects.filter(pk=to_do.pk).update(
            note=f"{to_do.note} {line}".strip(),
            summary=f"{to_do.summary}; also {verdict.lot} ({verdict.number})"[:255])
    if not waiting:
        tell_the_quality_manager(
            recall_of, f"{recall_of.number}: {recall_of.lot} also made from {verdict.lot} ({verdict.number}), held",
            link=f"/quality/inspections/{recall_of.pk}", by=by, body=line)


def refile(lot, by=None):
    """
    A recall for `lot` if something inside it holds it and nothing waits
    for the Quality Manager's decision on it (review F6): its recall was
    voided while the source still held, or its own rejection was voided.
    Never stuck held with nothing to decide. Returns the recall or None.
    """
    if open_recall(lot) is not None or release_status(lot) != ReleaseStatus.HELD:
        return None
    holding = holding_sources(lot)
    own = latest_inspection(lot)
    if not holding or (own is not None and own.disposition not in RELEASING):
        return None
    plan = recall_plan(lot)
    if plan is None:
        return None
    made = _recall_one(holding[-1][1], lot, own, plan, by)
    # Filed under one source, it names every one that holds the lot (rereview 7): RB-J made from B2
    # and B3, both rejected, was filed again naming B3 alone, on exactly the lot that has two.
    for _source, verdict in holding[:-1]:
        also_held(made, verdict, by)
    return made


def _where(lot):
    unit = lot.item.uom.code if lot.item.uom_id else ""
    in_stock, gone = where_it_is(lot)
    stock = (", ".join(f"{_figure(quantity)} {unit} in {warehouse}" for warehouse, quantity in in_stock)
             or "nothing in stock")
    went = (", gone: " + ", ".join(f"{_figure(quantity)} {unit} on {reference}" for reference, quantity in gone)
            if gone else "")
    return f"{lot} ({stock}{went})"


def _recall_one(verdict, lot, own, plan, by):
    from .decisions import tell_the_quality_manager

    source, today = verdict.lot, timezone.localdate()
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
    return recall_of


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
    transaction.on_commit(lambda: lift(lot, why))


def lift(lot, why):
    """The lift itself, in its own transaction (see `lift_when_committed`)."""
    with transaction.atomic():
        lots = lots_made_from(lot)
        # Under the very locks a new verdict on the source takes (Inspection.post: the batch,
        # what it was made from, everything made from it, in one key order), and read afresh
        # after them: a lift and a new hold are one after the other (review F3).
        lock_rows(*lot_and_sources(lot), *lots, refresh=False)
        for recall_of in Inspection.objects.filter(
                lot_id__in=[each.pk for each in lots], posted=True, voided_at__isnull=True,
                recalled_by__isnull=False, disposition=Disposition.AWAITING).order_by("pk"):
            asked = timezone.now()
            if recall_stands(recall_of):
                continue
            # Voided only on what was read: a hold posted since keeps it (it found the recall
            # open and told the Quality Manager on it).
            inside = [each.pk for each in lot_and_sources(recall_of.lot)[1:]]
            if not Inspection.objects.filter(
                    lot_id__in=inside, posted=True, voided_at__isnull=True, posted_at__gte=asked,
                    disposition__in=HOLDING).exists():
                recall_of.void(f"The recall is lifted: {why}"[:255])


