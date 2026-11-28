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


import contextlib
import contextvars
import datetime

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
    return _remembered(("plan", item.pk, on_date), lambda: item.inspection_plans.filter(
        Q(valid_from__isnull=True) | Q(valid_from__lte=on_date),
        Q(valid_to__isnull=True) | Q(valid_to__gte=on_date),
        is_active=True,
    ).first())


# Where a batch was re-made from other batches (split or joined), the
# module that knows says which: `fn(lot) -> [lot, ...]`. Registered so
# quality asks without importing the manufacturing that does it.
LOT_SOURCES = []
# The same lineage the other way: `fn(lot) -> [lot, ...]`, the batches re-made from `lot`.
LOTS_MADE_FROM = []


def register_lot_sources(provider, made_into):
    """
    One lineage, both ways, in one call: `provider(lot)`, the batches `lot`
    was re-made from, and `made_into(lot)`, the batches re-made from it.

    Both are asked: the gate holds a batch whatever is inside it (upward),
    and a recall holds whatever was made from a batch rejected later
    (downward, O207). Registered one way only, a lineage would be a door
    the recall cannot find, so the second is not optional.
    """
    if provider not in LOT_SOURCES:
        LOT_SOURCES.append(provider)
    if made_into not in LOTS_MADE_FROM:
        LOTS_MADE_FROM.append(made_into)


# Who supplied a batch (L28): `fn(lot) -> Supply | None`, the vendor and the
# day of the receipt that brought it in. Registered so quality asks without
# importing purchasing. None is a batch nobody sold the plant: made here, or
# a customer's return, which takes no supplier's certificate.
LOT_SUPPLIERS = []


class Supply:
    """Who brought a batch in, on what day, on which receipt."""

    def __init__(self, vendor, received_on, reference):
        self.vendor, self.received_on, self.reference = vendor, received_on, reference


def register_lot_supplier(provider):
    if provider not in LOT_SUPPLIERS:
        LOT_SUPPLIERS.append(provider)


def supplied_by(lot):
    """The Supply that brought `lot` in from a vendor, or None."""
    def work():
        for provider in LOT_SUPPLIERS:
            found = provider(lot)
            if found is not None:
                return found
        return None

    return _remembered(("supplied", lot.pk), work)


def standing_certificate(lot):
    """The supplier's certificate of analysis standing for `lot` (posted, not voided), or None."""
    from .certificates import SupplierCertificate

    return _remembered(("certificate", lot.pk), lambda: SupplierCertificate.objects.filter(
        lot=lot, posted=True, voided_at__isnull=True).select_related("vendor").first())


def certificate_plan(lot):
    """
    The plan a supplier's certificate of `lot` is read against: the one in
    force on the day its receipt brought it in, or None (made here, or no
    plan that day). Whether a certificate is required is that plan's to say.
    """
    supply = supplied_by(lot)
    return plan_for(lot.item, supply.received_on) if supply is not None else None


def certificate_conceded(lot, certificate):
    """
    Whether the Quality Manager took `lot` as it is after `certificate` was
    posted: a concession on its standing inspection, decided or posted after
    the certificate. A pass is not one: a passed inspection alone does not
    override a certificate outside (L28). A concession taken before the
    certificate was known did not know it.
    """
    own = latest_inspection(lot)
    return (own is not None and own.disposition == Disposition.CONCESSION
            and _took_effect(own, decided=True) > certificate.posted_at)


def certificate_failed(lot):
    """
    The standing certificate of `lot` that is outside its plan (or missing a
    value) where that plan requires one and nobody has conceded it, or None.
    What an inspection's pass of `lot` waits for the Quality Manager over.
    """
    plan = certificate_plan(lot)
    if plan is None or not plan.certificate_required:
        return None
    certificate = standing_certificate(lot)
    if certificate is None or certificate.is_within() or certificate_conceded(lot, certificate):
        return None
    return certificate


def certificate_words(lot, action="issue"):
    """
    Why the supplier's certificate holds `lot`, in words, or "" (L28). Asked
    only where the plan in force on its receipt's day requires a certificate:
    none standing holds it, and one outside or missing a value holds it until
    the Quality Manager concedes it on an inspection. One within decides
    nothing more: the batch still needs whatever its own inspection needs.
    Derived from the standing certificate, never stored: voided, the question
    goes back to "none recorded".
    """
    plan = certificate_plan(lot)
    if plan is None or not plan.certificate_required:
        return ""
    supply = supplied_by(lot)
    certificate = standing_certificate(lot)
    if certificate is None:
        return (f"{lot} came in from {supply.vendor} on {supply.reference} and {plan} asks for the "
                f"supplier's certificate of analysis. None is recorded, so it cannot {action} until "
                "quality records and posts one.")
    if certificate_failed(lot) is None:
        return ""
    return (f"{lot} is held: {certificate.number}, the certificate {certificate.supplier_number} from "
            f"{supply.vendor}, is {certificate.verdict_words()}. It cannot {action} "
            "until the Quality Manager takes it by concession on an inspection.")


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


# How far a recall follows what was made from a batch: four levels reach a
# sack from polymer in this plant (polymer, tape, fabric, sack), as the
# genealogy and the trace walk (manufacturing/trace.py reads this one).
LINEAGE_DEPTH = 4

# One question to the gate reads each batch's standing and lineage once:
# the lineage of a sack runs through fabric, tape and polymer, and each
# step's status asks the steps above it again. Kept for one question only,
# never across a write.
_ASKED = contextvars.ContextVar("quality_release_asked", default=None)


@contextlib.contextmanager
def one_question():
    if _ASKED.get() is not None:
        yield
        return
    token = _ASKED.set({"asking": [], "cut": False})
    try:
        yield
    finally:
        _ASKED.reset(token)


def _remembered(key, work):
    asked = _ASKED.get()
    if asked is None:
        return work()
    if key in asked:
        return asked[key]
    found = work()
    # Once a loop has been cut in this question, what is worked out after
    # leans on a batch that was still being asked: answered, not remembered.
    if not asked["cut"]:
        asked[key] = found
    return found


def walk(lot, step, depth=None, beyond=None):
    """
    The one walk of a lineage, up or down: [(batch, level)], `lot` first at
    level 0, each batch once whatever path reaches it, so a loop of any
    length (two runs feeding each other's regrind, a re-batch of a re-batch)
    ends. `step(batch)` gives the next batches. Followed `depth` levels (all,
    when None); a batch at the last level with batches not yet seen beyond
    it is added to `beyond` when a list is given. The gate's lineage both
    ways and the trace's walk down (manufacturing/trace.py) all go by it.
    """
    found, waiting, seen = [], [(lot, 0)], {lot.pk}
    while waiting:
        each, level = waiting.pop(0)
        found.append((each, level))
        if depth is not None and level >= depth:
            if beyond is not None and any(child is not None and child.pk not in seen
                                          for child in step(each)):
                beyond.append(each)
            continue
        for child in step(each):
            if child is not None and child.pk not in seen:
                seen.add(child.pk)
                waiting.append((child, level + 1))
    return found


def prefill_page(lots):
    """
    What the gate reads of each batch, read for a page of them at once
    (inventory.tracking.held_because_page, O231): the standing verdict, the
    plan of each item, and what each lineage says it was made from, a
    provider with a page form (`provider.for_page(lots) -> {pk: [lot]}`)
    asked once for the page. Called inside the page's one question.
    """
    asked = _ASKED.get()
    if asked is None or not lots:
        return
    pks = [lot.pk for lot in lots]
    standing = (Inspection.objects.filter(lot__in=pks, posted=True, voided_at__isnull=True)
                .exclude(recalled_by__isnull=False, disposition=Disposition.AWAITING)
                .order_by("lot_id", "-inspected_on", F("posted_at").desc(nulls_last=True), "-id"))
    latest = {}
    for inspection in standing:
        latest.setdefault(inspection.lot_id, inspection)
    for pk in pks:
        asked.setdefault(("latest", pk, False), latest.get(pk))
    recalls = {}
    for recall in Inspection.objects.filter(lot__in=pks, posted=True, voided_at__isnull=True,
                                            recalled_by__isnull=False,
                                            disposition=Disposition.AWAITING).order_by("pk"):
        recalls.setdefault(recall.lot_id, recall)
    for pk in pks:
        asked.setdefault(("open_recall", pk), recalls.get(pk))
    today = timezone.localdate()
    items = {lot.item_id: lot.item for lot in lots}
    plans = {}
    for plan in InspectionPlan.objects.filter(
            Q(valid_from__isnull=True) | Q(valid_from__lte=today),
            Q(valid_to__isnull=True) | Q(valid_to__gte=today),
            item_id__in=list(items), is_active=True).order_by("pk"):
        plans.setdefault(plan.item_id, plan)
    for item_pk in items:
        asked.setdefault(("plan", item_pk, today), plans.get(item_pk))
    _prefill_certificates(asked, lots, pks)
    found = {pk: [] for pk in pks}
    for provider in LOT_SOURCES:
        page = getattr(provider, "for_page", None)
        answers = page(lots) if page is not None else {lot.pk: provider(lot) for lot in lots}
        for pk in pks:
            found[pk].extend(answers.get(pk, []))
    for pk in pks:
        asked.setdefault(("sources", pk), found[pk])


def _prefill_certificates(asked, lots, pks):
    """
    What the supplier's certificate asks of a page of batches (L28), read once
    for the page: who supplied each (a provider's `for_page(lots) -> {pk: Supply}`
    where it has one), the plan of each item on each receipt's day, and the
    standing certificate of each.
    """
    from django.db.models import Prefetch

    from .certificates import SupplierCertificate, SupplierCertificateLine

    supplied = {}
    for provider in LOT_SUPPLIERS:
        page = getattr(provider, "for_page", None)
        answers = page(lots) if page is not None else {lot.pk: provider(lot) for lot in lots}
        for pk in pks:
            if supplied.get(pk) is None:
                supplied[pk] = answers.get(pk)
    for pk in pks:
        asked.setdefault(("supplied", pk), supplied.get(pk))
    standing = {}
    # Its words name its lines (verdict_words): read with the page, not a lot at a time (O434).
    certificates = (SupplierCertificate.objects.filter(lot__in=pks, posted=True, voided_at__isnull=True)
                    .select_related("vendor").order_by("pk")
                    .prefetch_related(Prefetch("lines", queryset=SupplierCertificateLine.objects
                                               .select_related("characteristic"))))
    for certificate in certificates:
        standing.setdefault(certificate.lot_id, certificate)
    for pk in pks:
        asked.setdefault(("certificate", pk), standing.get(pk))
    days = {(lot.item_id, supplied[lot.pk].received_on) for lot in lots if supplied.get(lot.pk) is not None}
    if not days:
        return
    plans = list(InspectionPlan.objects.filter(item_id__in={item for item, _day in days}, is_active=True)
                 .order_by("pk"))
    for item_pk, day in days:
        asked.setdefault(("plan", item_pk, day), next(
            (plan for plan in plans if plan.item_id == item_pk
             and (plan.valid_from is None or plan.valid_from <= day)
             and (plan.valid_to is None or plan.valid_to >= day)), None))


def lot_sources(lot):
    def work():
        found = []
        for provider in LOT_SOURCES:
            found.extend(provider(lot))
        return found

    return list(_remembered(("sources", lot.pk), work))


def made_into(lot):
    """The batches re-made from `lot` directly, by every lineage registered."""
    return [child for provider in LOTS_MADE_FROM for child in provider(lot)]


def lots_made_from(lot, depth=None, beyond=None):
    """
    Every batch made from `lot`, directly or at a remove, each once,
    nearest first; not `lot`. Followed `depth` levels (all, when None),
    `beyond` as `walk` says.
    """
    return [each for each, _level in walk(lot, made_into, depth=depth, beyond=beyond)[1:]]


def latest_inspection(lot, measured=False):
    """
    The inspection that currently speaks for this batch: the latest dated,
    and of two on one day the one posted last (O145). Ranked by id, a
    rejection drafted first and posted after the day's pass stood beside it
    unheard, and the lot read released. Posting is the decision; drafting
    is not. An old row with no posting time ranks after those with one.

    A recall the Quality Manager has not decided yet does not speak (O207):
    the batch is held by what is inside it for as long as that is held, a
    hold read from the source's verdict and not stored here, so the source
    voided or passed again hands the batch back to its own pass. Decided,
    the recall is the batch's verdict like any other. `measured`: the
    latest that measured something, recalls left out (a certificate's).
    """
    def work():
        standing = Inspection.objects.filter(lot=lot, posted=True, voided_at__isnull=True).exclude(
            recalled_by__isnull=False, disposition=Disposition.AWAITING)
        if measured:
            standing = standing.filter(recalled_by__isnull=True)
        return standing.order_by("-inspected_on", F("posted_at").desc(nulls_last=True), "-id").first()

    return _remembered(("latest", lot.pk, measured), work)


def recall_plan(lot):
    """The plan a recall of `lot` is filed under: its own standing inspection's, else the one in force."""
    own = latest_inspection(lot)
    return own.plan if own is not None else plan_for(lot.item)


def open_recall(lot):
    """The recall of `lot` waiting for the Quality Manager's decision, or None."""
    # Read off the page when a page of batches was asked at once (prefill_page), and only
    # then: a recall writes recalls inside its question, and must read them as they are.
    asked = _ASKED.get()
    if asked is not None and ("open_recall", lot.pk) in asked:
        return asked[("open_recall", lot.pk)]
    return Inspection.objects.filter(
        lot=lot, posted=True, voided_at__isnull=True, recalled_by__isnull=False,
        disposition=Disposition.AWAITING).order_by("pk").first()


# The verdicts that hold a batch.
HOLDING = (Disposition.REJECT, Disposition.REWORK, Disposition.AWAITING)
RELEASING = (Disposition.ACCEPT, Disposition.CONCESSION)


def _took_effect(inspection, decided=False):
    """When a verdict took effect: when it was posted, or `decided`, when the Quality Manager decided it."""
    if decided and inspection.decided_at is not None:
        return inspection.decided_at
    if inspection.posted_at is not None:
        return inspection.posted_at
    return timezone.make_aware(datetime.datetime.combine(inspection.inspected_on, datetime.time.min))


def recalled_from(lot):
    """
    [(batch, verdict)]: the batches `lot` was re-made from (at any remove)
    whose standing verdict holds them and came after `lot`'s own release
    (O207). A-1 passed on 15 January; F-1, inside it, was rejected on 2
    February: A-1's pass predates what is now known about what is inside
    it, so A-1 is held. Empty when `lot` is not released by its own verdict
    (it then stands as its sources do, or is held already), or when every
    hold inside it came first: a release taken knowing them (the Quality
    Manager's decision, or a pass they posted) is not recalled by them.

    Derived, never stored: the source's rejection voided, or the source
    passed again, and the batch reads released again by its own pass.
    """
    own = latest_inspection(lot)
    if own is None or own.disposition not in RELEASING:
        return []
    released = _took_effect(own, decided=True)
    found = []
    for source in lot_and_sources(lot)[1:]:
        verdict = latest_inspection(source)
        if verdict is not None and verdict.disposition in HOLDING and _took_effect(verdict) > released:
            found.append((source, verdict))
    return found


def holding_sources(lot):
    """[(batch, verdict)]: the batches `lot` was made from, at any remove, whose standing verdict holds them."""
    return [(source, verdict) for source in lot_and_sources(lot)[1:]
            if (verdict := latest_inspection(source)) is not None and verdict.disposition in HOLDING]


def still_held_words(lot, action="go"):
    """Why `lot` is held, in the gate's words, wherever it is; "" when nothing holds it."""
    with one_question():
        return _why_held(lot.item, lot, warehouse=None, action=action) if release_status(lot) == ReleaseStatus.HELD else ""


def verdict_words(verdict):
    """'rejected on 2 February 2026 (QC-12)': what a verdict that holds said, and when."""
    day = f"{verdict.inspected_on.day} {verdict.inspected_on:%B %Y}"
    if verdict.disposition == Disposition.AWAITING:
        return f"held on {day} ({verdict.number}) for the Quality Manager's decision"
    if verdict.disposition == Disposition.REWORK:
        return f"held for rework on {day} ({verdict.number})"
    return f"rejected on {day} ({verdict.number})"


def recall_words(lot, recalled):
    """Why a recalled batch is held, in words: 'made from F-1, rejected on 2 February 2026 (QC-12)'."""
    return "; ".join(f"made from {source}, {verdict_words(verdict)}" for source, verdict in recalled)


def lot_and_sources(lot):
    """`lot`, then every batch it was re-made from, theirs in turn, each once (`walk`)."""
    return [each for each, _level in walk(lot, lot_sources)]


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
    Where this batch stands (see `_release_status`), each batch of its
    lineage read once. A batch asked again while its own answer is still
    being worked out (a loop: RG-1 into a run that made RG-2, RG-2 into one
    that made RG-1) answers as adding nothing: no batch is held by itself
    through a loop, and whatever holds the loop holds it from outside it.
    """
    with one_question():
        asked = _ASKED.get()
        if ("status", lot.pk) in asked:
            return asked[("status", lot.pk)]
        if lot.pk in asked["asking"]:
            asked["cut"] = True
            return ReleaseStatus.RELEASED
        asked["asking"].append(lot.pk)
        try:
            return _remembered(("status", lot.pk), lambda: _release_status(lot))
        finally:
            asked["asking"].pop()


def _release_status(lot):
    """
    Where this batch stands.

    Rework and rejection are both held: a roll waiting to be re-wound is
    not stock anybody may draw on, however likely it is to pass the
    second time.

    A batch re-made from others and not inspected itself stands as they
    do: released only if all of them are, held if any is. Mixing a held
    batch into passed ones holds the lot; it never passes the held sacks.
    """
    status = _own_status(lot)
    # The supplier's certificate holds a batch its own verdict would let go (L28).
    if status != ReleaseStatus.HELD and certificate_words(lot):
        return ReleaseStatus.HELD
    return status


def _own_status(lot):
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
    if inspection.disposition in RELEASING:
        # Released by its own verdict, unless something inside it was held after (O207), or a
        # recall still waits on what is inside it (review F4: its own failure taken as is does
        # not take what is inside it as is).
        if recalled_from(lot) or (open_recall(lot) is not None and holding_sources(lot)):
            return ReleaseStatus.HELD
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

    # A pass waits too when what it was made from is held (review F3), or its supplier's
    # certificate is outside (L28).
    verdict = "passed" if held.result == Result.PASS else "failed"
    inside = held_inside(lot) if verdict == "passed" else []
    certificate = certificate_failed(lot) if verdict == "passed" else None
    made_from = (f", but it is made from {', '.join(str(source) for source in inside)}, "
                 "which quality holds," if inside else "")
    if certificate is not None:
        made_from += f", but its supplier's certificate {certificate.number} is {certificate.verdict_words()},"
    return (f"{lot} is held: it {verdict} {held.number} on {held.inspected_on}{made_from} and waits "
            f"for the Quality Manager's decision. It cannot {action} until they decide.")


def why_held(item, lot, warehouse=None, action="issue", reworking=None):
    """The one release gate: see `_why_held`. Each batch of the lineage is read once a question."""
    with one_question():
        return _why_held(item, lot, warehouse=warehouse, action=action, reworking=reworking)


def _why_held(item, lot, warehouse=None, action="issue", reworking=None):
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
    if status == ReleaseStatus.HELD and not (held is not None and held.disposition in HOLDING):
        # Its own verdict does not hold it: the supplier's certificate may (L28).
        said = certificate_words(lot, action)
        if said:
            return said
    if status == ReleaseStatus.HELD:
        if held is None:
            holding = holding_sources(lot)
            if holding:
                return (f"{lot} is held: it was re-made from a batch that is held "
                        f"({recall_words(lot, holding)}). Inspect it before it can {action}.")
            return (f"{lot} is held: it was re-made from a batch that is held or not "
                    f"yet inspected. Inspect it before it can {action}.")
        if held.disposition in RELEASING:
            # Recalled (O207): its own release came before the hold on what is inside it, or a
            # recall waits on what is inside it.
            waiting = open_recall(lot)
            decides = (f"The Quality Manager decides it ({waiting.number})." if waiting is not None
                       else "Nothing waits for the Quality Manager's decision on it: they re-inspect it.")
            return (f"{lot} is held: {recall_words(lot, recalled_from(lot) or holding_sources(lot))}, "
                    f"after its own release ({held.number}). {decides} It cannot {action} until then.")
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
