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
from django.db.models import Q
from django.utils import timezone

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


def lot_sources(lot):
    found = []
    for provider in LOT_SOURCES:
        found.extend(provider(lot))
    return found


def latest_inspection(lot):
    """The inspection that currently speaks for this batch."""
    return Inspection.objects.filter(
        lot=lot, posted=True, voided_at__isnull=True
    ).order_by("-inspected_on", "-id").first()


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
    if lot is not None and reworking is not None and lot.pk == reworking.pk:
        return ""
    status = release_status(lot) if lot is not None else None
    if status == ReleaseStatus.HELD:
        held = latest_inspection(lot)
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


def may_be_drawn(item, lot):
    """Whether the gate lets this batch go, for whatever counts or chooses usable stock."""
    return not why_held(item, lot)
