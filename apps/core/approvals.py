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

from .models import serialised


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
