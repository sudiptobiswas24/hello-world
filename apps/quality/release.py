"""
Whether a batch may leave.

One invariant, in one place: a batch of something that must be
inspected does not go into a run or onto a lorry until somebody has
measured it and passed it. It is written here rather than in each
document for the same reason the negative-stock rule was pulled out of
four of them — a fifth copy is how the fifth copy starts.

**Not yet inspected is not passed.** The commonest way a quality module
turns into a filing cabinet is by treating an absent inspection as
consent: the plan exists, nobody has run it, and the goods ship anyway.
So the default answer for a mandatory plan is no.

The status is derived from the latest standing inspection rather than
stored on the lot. Voiding an inspection then hands the question back
to the one before it, or to nobody — which is "not yet inspected",
which is no. A stored flag would have had to remember to do that.
"""


from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.utils import timezone

from apps.core.models import lock_rows

from .models import Disposition, Inspection, InspectionPlan, ReleaseStatus


def plan_for(item, on_date=None):
    """
    The inspection plan in force for an item on `on_date` (today when not
    given), or None.

    Today is the right default for everything that asks it: whether a
    batch may move now, and whether stock on hand is usable now. At most
    one answers, because overlapping windows are refused where a plan is
    saved.
    """
    on_date = on_date or timezone.localdate()
    return item.inspection_plans.filter(
        Q(valid_from__isnull=True) | Q(valid_from__lte=on_date),
        Q(valid_to__isnull=True) | Q(valid_to__gte=on_date),
        is_active=True,
    ).first()


# Where a batch was re-made from other batches (split or joined), the
# module that knows says which: `fn(lot) -> [lot, ...]`. Registered so
# quality asks without importing the manufacturing that does it.
LOT_SOURCES = []


def register_lot_sources(provider):
    if provider not in LOT_SOURCES:
        LOT_SOURCES.append(provider)


# Who wants to hear that a batch has a new verdict (posted or decided): the
# module that holds something on it, `fn(lot, by)`. Registered so quality
# tells without importing it: a receipt's to-dos close when nothing on it is
# held any more (purchasing).
LOT_DECIDED = []


def register_lot_decided(listener):
    if listener not in LOT_DECIDED:
        LOT_DECIDED.append(listener)


def lot_decided(lot, by=None):
    for listener in LOT_DECIDED:
        listener(lot, by)


def lot_sources(lot):
    found = []
    for provider in LOT_SOURCES:
        found.extend(provider(lot))
    return found


def latest_inspection(lot):
    """
    The inspection that currently speaks for this batch: the latest dated,
    and of two on one day the one posted last (O145). Ranked by id, a
    rejection drafted first and posted after the day's pass stood beside it
    unheard, and the lot read released. Posting is the decision; drafting
    is not. An old row with no posting time ranks after those with one.
    """
    return Inspection.objects.filter(
        lot=lot, posted=True, voided_at__isnull=True
    ).order_by("-inspected_on", F("posted_at").desc(nulls_last=True), "-id").first()


def lot_and_sources(lot):
    """`lot`, then every batch it was re-made from, theirs in turn, each once."""
    found, waiting, seen = [], [lot], set()
    while waiting:
        each = waiting.pop(0)
        if each is None or each.pk in seen:
            continue
        seen.add(each.pk)
        found.append(each)
        waiting.extend(lot_sources(each))
    return found


def standing_verdicts(lot, lock=False):
    """
    [(batch, inspection)]: the verdict standing for `lot` and for each batch
    it was re-made from, its own first (O146). A re-inspection follows all
    of them: PP-A rejected on 1 June and split into PP-A2 was passed by an
    inspection of PP-A2 dated 25 May, read beside PP-A2's own inspections
    only, of which there were none. `lock`: hold the batches first, in the
    one lock order, so two inspections posting at once each see the other.
    """
    lots = lot_and_sources(lot)
    if lock:
        lock_rows(*lots, refresh=False)
    return [(each, standing) for each in lots
            if (standing := latest_inspection(each)) is not None]


def release_status(lot):
    """
    Where this batch stands.

    Rework and rejection are both held: a roll waiting to be re-wound is
    not stock anybody may draw on, however likely it is to pass the
    second time.

    A batch re-made from others and not inspected itself stands as they
    do: released only if all of them are, held if any is. Mixing a held
    batch into passed ones holds the lot; it never passes the held sacks.
    """
    inspection = latest_inspection(lot)
    if inspection is None:
        sources = lot_sources(lot)
        if not sources:
            return ReleaseStatus.UNINSPECTED
        statuses = {release_status(source) for source in sources}
        for status in (ReleaseStatus.HELD, ReleaseStatus.UNINSPECTED):
            if status in statuses:
                return status
        return ReleaseStatus.RELEASED
    if inspection.disposition in (Disposition.ACCEPT, Disposition.CONCESSION):
        return ReleaseStatus.RELEASED
    return ReleaseStatus.HELD


def held_inside(lot):
    """
    The batches `lot` was re-made from (split, joined, or assembled by a
    subcontractor) that quality holds now. A pass of `lot` cannot show a
    defect inside them, so it is the Quality Manager's to release (decision
    22, review F3), as a concession on them would be.
    """
    return [source for source in lot_sources(lot) if release_status(source) == ReleaseStatus.HELD]


def awaiting_decision(lot, action="issue"):
    """
    Why `lot` waits for the Quality Manager's decision, in words, or "":
    its standing inspection failed under an advisory plan and nobody has
    decided it. Said by the gate, and by a rework run, which would decide it.
    """
    held = latest_inspection(lot) if lot is not None else None
    if held is None or held.disposition != Disposition.AWAITING:
        return ""
    from .models import Result

    # A pass waits too when what it was made from is held (review F3).
    verdict = "passed" if held.result == Result.PASS else "failed"
    made_from = (f", but it is made from {', '.join(str(source) for source in held_inside(lot))}, "
                 "which quality holds," if verdict == "passed" else "")
    return (f"{lot} is held: it {verdict} {held.number} on {held.inspected_on}{made_from} and waits "
            f"for the Quality Manager's decision. It cannot {action} until they decide.")


def why_held(item, lot, warehouse=None, action="issue", reworking=None):
    """
    Why `lot` of `item` may not be drawn from `warehouse`, in words, or "".

    The one release gate. Every way a batch goes into a run or onto a
    lorry asks it: a delivery, a material issue, a backflush (which is an
    issue), a tape load or a roll mount on any run, a pick that chooses
    batches for itself, and planning, which counts only what passes it.
    Each door that answered for itself was a door a held lot walked
    through: a backflushed loom loaded a rejected doff because only the
    issue asked, and production drew from the quarantine bay because
    only a delivery looked at the warehouse.

    **The standing verdict holds, whatever the plan says today.** A batch
    rejected or held for rework stays held when its plan is retired or
    made advisory: the plan says what must be measured from now on, and
    retiring it measured nothing. Today's plan answers only the question
    no verdict has answered yet: whether a batch nobody inspected may go.

    `reworking` is the batch a rework run was raised for: that run may
    draw it held, as no other may (see `MaterialIssueLine`).
    """
    if warehouse is not None and warehouse.is_quarantine:
        return (f"{warehouse} holds goods awaiting inspection; accept them into a "
                f"store before they can {action}.")
    # Not even by a rework run: whether it is reworked is the Quality
    # Manager's to decide, and a run drawing it would decide it for them.
    said = awaiting_decision(lot, action)
    if said:
        return said
    held = latest_inspection(lot) if lot is not None else None
    if lot is not None and reworking is not None and lot.pk == reworking.pk:
        return ""
    status = release_status(lot) if lot is not None else None
    if status == ReleaseStatus.HELD:
        if held is None:
            return (f"{lot} is held: it was re-made from a batch that is held or not "
                    f"yet inspected. Inspect it before it can {action}.")
        return (f"{lot} is held: {held.get_disposition_display().lower()} on "
                f"{held.inspected_on} ({held.number}). It cannot {action}.")
    plan = plan_for(item)
    if plan is None or not plan.is_mandatory:
        # Most of what a plant moves is not inspected, and nothing has
        # said this batch is bad: this must not stand in its way.
        return ""
    if lot is None:
        return (f"{item} has a mandatory inspection plan, so it moves by batch and "
                f"this line does not name one. Nothing can {action} a quantity "
                "nobody can point at a measurement for.")
    if status == ReleaseStatus.UNINSPECTED:
        return (f"{lot} has not been inspected and {item} says it must be. Not yet "
                f"inspected is not passed, so it cannot {action} yet.")
    return ""


def check_released(item, lot, action="issue", warehouse=None, reworking=None):
    """Refuse what `why_held` says may not be drawn."""
    said = why_held(item, lot, warehouse=warehouse, action=action, reworking=reworking)
    if said:
        raise ValidationError(said)


def may_be_drawn(item, lot, warehouse=None):
    """
    Whether the gate lets this batch go from `warehouse`, for whatever
    counts or chooses usable stock. Asked without the warehouse it cannot
    see a quarantine bay, which planning then counted as cover wherever
    its caller forgot to leave the bay out (audit_invariants reports it).
    """
    return not why_held(item, lot, warehouse=warehouse)
