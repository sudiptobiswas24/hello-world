"""
Approval: the part that is the same wherever a document needs a second
pair of eyes.

Role permissions answer *who may act on a document*. They say nothing
about how far the actor may go while doing it — how much margin a rep
may give away, how much money a buyer may commit. Those are amount
questions, and no role check anywhere notices them.

The thresholds differ by document, so each one supplies its own
`approval_reasons()`. Everything else — recording who approved and when,
refusing to approve what needs no approval, dropping an approval when
the document changes underneath it — is identical, and lives here so the
two sides cannot drift into approving differently.

Lives in Core because it imports nothing from any module: the hook is
abstract and the concrete thresholds stay with the document.

A second pair of eyes is a different person's. Nothing asked who had
raised what was approved: an AR Manager approved their own order at 40%
against a 15% policy, and an AP Manager their own requisition. Nobody
approves what they made or changed (`check_not_raised_by`), the one rule
for orders, purchase orders, requisitions and a concession on a batch.
"""

from collections import defaultdict

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .models import check_login_acts, serialised


def authors(document, *parts):
    """
    The pks of every login that made or changed `document` or any row of
    its `parts` (related names: what it is made of, such as its lines):
    their own stamps and their history, which keeps every change and not
    only the last. A revision (`revision_of`) counts the authors of what
    it revises, back to the first: a quote's writer revised it and
    approved the revision, which had been copied in code with no names.
    """
    from django.contrib.contenttypes.models import ContentType

    from .history import EventKind, RecordEvent

    rows = [document, *(row for name in parts for row in getattr(document, name).all())]
    found = {getattr(row, name, None) for row in rows for name in ("created_by_id", "updated_by_id")}
    by_kind = defaultdict(set)
    for row in rows:
        by_kind[ContentType.objects.get_for_model(type(row))].add(row.pk)
    for kind, pks in by_kind.items():
        found.update(RecordEvent.objects.filter(
            content_type=kind, object_id__in=pks, kind__in=[EventKind.CREATED, EventKind.UPDATED],
        ).values_list("who_id", flat=True))
    earlier = getattr(document, "revision_of", None)
    if earlier is not None:
        found |= authors(earlier, *parts)
    found.discard(None)
    return found


def check_not_raised_by(by, raised_by, document):
    """
    Refuse an approver among those who raised `document` (`raised_by`, pks
    of logins). A superuser is not asked: they hold every right and the
    admin besides, and a rule that cannot bind them would only pretend to.
    """
    if by is None or by.is_superuser:
        return
    if by.pk in raised_by:
        raise ValidationError(
            f"{by.get_username()} raised or changed {document}; somebody else approves it.")


class ApprovalStatus(models.TextChoices):
    NOT_REQUIRED = "not_required", "Not required"
    PENDING = "pending", "Awaiting approval"
    APPROVED = "approved", "Approved"


class ApprovableMixin(models.Model):
    """
    Records an approval against a document. Subclasses implement
    `approval_reasons()` and decide when to consult `approval_status()`.
    """

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    approval_note = models.CharField(max_length=255, blank=True, editable=False)

    # What an approver reads besides the document itself, by related name:
    # whoever wrote a line wrote what is approved.
    approval_parts = ("lines",)

    class Meta:
        abstract = True

    def raised_by(self):
        """The logins that made or changed this document or its parts: never its approver."""
        return authors(self, *self.approval_parts)

    def approval_reasons(self):
        """
        Every policy threshold this document breaches, in plain words.

        A list rather than a boolean because an approver needs to know
        what they are approving, and because a document that trips three
        limits should say so rather than reveal them one at a time as
        each is fixed.
        """
        raise NotImplementedError

    def requires_approval(self):
        return bool(self.approval_reasons())

    def approval_status(self):
        if self.approved_at:
            return ApprovalStatus.APPROVED
        return ApprovalStatus.PENDING if self.requires_approval() else ApprovalStatus.NOT_REQUIRED

    def can_be_approved(self):
        """Hook for states that make approval meaningless, e.g. cancelled."""
        return True

    def check_approver(self, by):
        """
        Hook: refuse an approver who lacks the authority for this document.

        Separate from `can_be_approved`, which is about the document's
        state. This is about the person, and most documents do not care —
        but where an amount decides who may sign, the check belongs next
        to the approval rather than in whichever view happens to call it.
        """
        return

    @serialised("approved_at")
    def approve(self, by=None, note=""):
        """
        Record that someone accepted the breach: one at a time, and against
        the document as it stands under the lock, so a line added while the
        approver read waits and then withdraws the approval it would have
        slipped under.
        """
        if not self.can_be_approved():
            raise ValidationError("This document cannot be approved in its current state.")
        check_login_acts(by)
        self.check_approver(by)
        check_not_raised_by(by, self.raised_by(), self)
        if self.approved_at:
            raise ValidationError("This document has already been approved.")
        if not self.requires_approval():
            raise ValidationError("This document breaches no policy; it needs no approval.")
        self.approved_by = by
        self.approved_at = timezone.now()
        self.approval_note = note or "; ".join(self.approval_reasons())[:255]
        self.save(update_fields=["approved_by", "approved_at", "approval_note", "updated_at"])

    @serialised("approved_at")
    def withdraw_approval(self):
        """
        Drop an approval so a changed document has to be looked at again.

        An approval covers the document someone actually read. Without
        this, approve at one price and edit to another.
        """
        if not self.approved_at:
            return
        self.approved_by = None
        self.approved_at = None
        self.approval_note = ""
        self.save(update_fields=["approved_by", "approved_at", "approval_note", "updated_at"])


def _kept(value):
    """policy_figures() as a JSONField keeps them: Decimals as exact text, keys as text."""
    if isinstance(value, dict):
        return {str(key): _kept(part) for key, part in value.items()}
    return None if value is None else str(value)


def _read_back(value):
    """_kept() read back: text to Decimal, a line's pk to int."""
    from decimal import Decimal

    if isinstance(value, dict):
        return {int(key) if key.isdigit() else key: (part if key == KEPT_IN else _read_back(part))
                for key, part in value.items()}
    return None if value is None else Decimal(value)


# The key under which kept figures name the currency they are in: the base's code (O337). Figures
# kept before O293 were in the order's own currency and named none.
KEPT_IN = "currency"


def base_code():
    from apps.core.models import Currency

    return Currency.objects.filter(is_base=True).values_list("code", flat=True).first() or "base"


def _figure(figures, path):
    for step in path:
        if not isinstance(figures, dict):
            return None
        figures = figures.get(step)
    return figures


class ChangesWithinItsApproval:
    """
    A confirmed document asked its approval policy again on every change to
    its lines, cuts and deletes included, and refused only where the change
    goes past what was already standing: the document as it was before the
    change (`before`, policy_figures()) or what an approval covered
    (`approved_figures`, kept when it was given), whichever reaches further.

    Asked of nothing, a sales line raised to 60% against a 15% policy
    shipped and invoiced (O72). Asked of everything, an order approved for
    one 20% line could not take one more of an undiscounted line; a cut
    that took it under the margin floor was never asked at all (O141).
    Purchasing withdrew a confirmed order's approval on any re-price and
    asked only the budget again (O167): one rule now, for both sides.

    The document supplies `policy_figures()`, each figure the policy weighs
    by name, a line's under its pk ({"total": 1000, "lines": {7: 600}}),
    and `policy_breaches()`, each figure past the policy as it stands now:
    (path into the figures, value, whether lower is worse, words). It has
    an `approved_figures` JSONField.
    """

    @serialised("approved_at")
    def approve(self, by=None, note=""):
        """Approve, and write down what the approval covered: a confirmed document changes within it."""
        super().approve(by=by, note=note)
        self.approved_figures = self.figures_as_kept()
        type(self)._base_manager.filter(pk=self.pk).update(approved_figures=self.approved_figures)

    def figures_as_kept(self):
        """policy_figures() as approved_figures keeps them, naming the base currency they are in (O337)."""
        return {**_kept(self.policy_figures()), KEPT_IN: base_code()}

    def _covered(self):
        """What the approval covered, or nothing when there is none standing."""
        kept = self.approved_figures if self.approved_at else None
        return _read_back(kept) if kept else {}

    def refuse_going_past_the_policy(self, before):
        covered = self._covered()
        reasons = []
        for path, value, low, words in self.policy_breaches():
            standing = [figure for figure in (_figure(before, path), _figure(covered, path)) if figure is not None]
            allowed = (min if low else max)(standing) if standing else None
            if allowed is None or (value < allowed if low else value > allowed):
                reasons.append(words)
        if reasons:
            raise PolicyRefusal(
                f"{self} is confirmed, and this change needs approval beyond what it has: ",
                *joined(reasons),
                " A confirmed order is not changed past the policy; take it as a new order, which "
                "asks for approval.")


def joined(reasons, between=" "):
    """The reasons with `between` among them, each kept as itself (a CostWords stays one)."""
    parts = []
    for reason in reasons:
        parts += [between, reason] if parts else [reason]
    return parts


class CostWords(str):
    """
    A breach's words that carry cost (a margin's figure), with `withheld`, the
    same breach in words that do not, and `seen_with`, the permission that is
    told the figure (O299). A str, so it joins and is kept as before; who is
    told what is decided on these attributes, never by comparing text.
    """

    def __new__(cls, words, withheld, seen_with):
        made = super().__new__(cls, words)
        made.withheld, made.seen_with = withheld, seen_with
        return made


def told(words, user):
    """`words` as `user` is told them: a CostWords' figure only to whoever holds its seen_with."""
    if isinstance(words, CostWords) and not (user is not None and user.has_perm(words.seen_with)):
        return words.withheld
    return str(words)


class PolicyRefusal(ValidationError):
    """
    A refusal made of a document's breaches. Its message is what anyone may
    read: a CostWords part in its withheld words, so a path that does not know
    who asked (an action re-raising exc.messages) tells no figure.
    told_to(user) is the message in full for whoever may see it; the API's
    exception handler answers with it. A Sales Rep pricing a confirmed order's
    line read "Gross margin is -700.00%", and each price was an oracle for
    the cost (O299).
    """

    def __init__(self, *parts):
        self.parts = parts
        super().__init__("".join(told(part, None) for part in parts))

    def told_to(self, user):
        return "".join(told(part, user) for part in self.parts)

    @property
    def in_full(self):
        return "".join(str(part) for part in self.parts)


def asks_its_policy_when_taxes_change(line_model):
    """
    A confirmed order's line asked the policy again when its taxes change, as
    when its price does (ChangesWithinItsApproval). A line's taxes are a
    many-to-many the serializer writes after the save, so save() never saw
    them: 900 confirmed under a 1,000 limit took GST to 1,062 on a clerk's
    PATCH, on either side (review F3). Weighed after the change against the
    order as it stood before it; refused in words, and the change goes with
    the refusal: Django writes a many-to-many in a transaction of its own, so
    the raise rolls it back. Called once for each line model whose order is a
    ChangesWithinItsApproval and whose line has `_policy_before()`.
    """
    from django.db import transaction
    from django.db.models.signals import m2m_changed

    def changed(sender, instance, action, reverse, pk_set, **kwargs):
        if reverse or not isinstance(instance, line_model):
            return
        if action in ("pre_add", "pre_remove", "pre_clear"):
            order = type(instance.order).objects.filter(pk=instance.order_id).first() if instance.order_id else None
            confirmed = order is not None and order.status == "confirmed"
            instance._taxes_before = order.policy_figures() if confirmed else None
            return
        if action not in ("post_add", "post_remove", "post_clear"):
            return
        before, instance._taxes_before = getattr(instance, "_taxes_before", None), None
        if before is None:
            return
        with transaction.atomic():
            order = type(instance.order).objects.select_for_update().get(pk=instance.order_id)
            order.refuse_going_past_the_policy(before)

    m2m_changed.connect(changed, sender=line_model.taxes.through, weak=False,
                        dispatch_uid=("asks-its-policy-when-taxes-change", line_model._meta.label))
