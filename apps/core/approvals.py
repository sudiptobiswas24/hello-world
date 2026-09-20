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
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


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

    class Meta:
        abstract = True

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

    def approve(self, by=None, note=""):
        """Record that someone accepted the breach."""
        if not self.can_be_approved():
            raise ValidationError("This document cannot be approved in its current state.")
        if self.approved_at:
            raise ValidationError("This document has already been approved.")
        if not self.requires_approval():
            raise ValidationError("This document breaches no policy; it needs no approval.")
        self.approved_by = by
        self.approved_at = timezone.now()
        self.approval_note = note or "; ".join(self.approval_reasons())[:255]
        self.save(update_fields=["approved_by", "approved_at", "approval_note", "updated_at"])

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
