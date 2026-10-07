"""
Engineering change orders on a bill of materials.

A recipe in use is never edited in place: a change is a new version
with a first day, raised against the version it replaces and worked on
while the old one still answers; applying it closes the old window the
day before and makes the new version the default in one step. Runs
already released keep what they froze when they were released; draft
runs due on or after the day move to the new version.
"""

import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, serialised, to_date

from .bom import BillOfMaterials, BomByproduct, BomComponent, BomSubstitute


class ChangeStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    APPLIED = "applied", "Applied"
    REJECTED = "rejected", "Rejected"


class BomChangeOrder(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    supersedes = models.ForeignKey(BillOfMaterials, on_delete=models.PROTECT, related_name="changes_from",
                                   help_text="The version being replaced.")
    draft = models.ForeignKey(BillOfMaterials, on_delete=models.PROTECT, related_name="changes_to",
                              help_text="The new version, made with the order and edited until it is applied.")
    effective_from = models.DateField(help_text="The first day output is made to the new version.")
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=ChangeStatus.choices, default=ChangeStatus.DRAFT)
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    decision_note = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="unique_bom_change_number"),
        ]
        permissions = [("apply_bomchangeorder", "Can apply or reject a BOM change order")]

    def __str__(self):
        return self.number or f"Change to {self.supersedes}"

    DECIDED = "a change order is decided once: apply or reject it, and raise another for anything further."

    def save(self, *args, **kwargs):
        # The stored status, not the instance's: the deciding save itself
        # finds "draft" stored and passes; everything after it is refused.
        if self.pk:
            was = BomChangeOrder.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if was != ChangeStatus.DRAFT:
                raise ValidationError(self.DECIDED)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A change order is not deleted; rejected, it stays as the record of what was not done.")

    def _decide(self, status, by, note):
        self.status = status
        self.decided_at = timezone.now()
        self.decided_by = by
        self.decision_note = note[:255]
        self.save(update_fields=["number", "status", "decided_at", "decided_by", "decision_note", "updated_at"])

    @serialised("status")
    def apply(self, by=None, note=""):
        """
        The new version takes over from its first day: the old window
        closes the day before, the new one is the default the old one
        was, and draft runs due from that day move across. Released runs
        keep what they froze.
        """
        from .orders import WorkOrder, WorkOrderStatus

        if self.status != ChangeStatus.DRAFT:
            raise ValidationError(f"This change is already {self.get_status_display().lower()}.")
        old, new = self.supersedes, self.draft
        if not new.components.exists():
            raise ValidationError(f"{new} has no inputs; a recipe with nothing in it is not applied.")
        with transaction.atomic():
            last_old_day = self.effective_from - datetime.timedelta(days=1)
            if old.valid_from is not None and last_old_day < old.valid_from:
                # The old version never had a day of its own: it goes, rather than run backwards.
                old.is_active = False
            elif old.valid_to is None or old.valid_to > last_old_day:
                old.valid_to = last_old_day
            old.save()
            new.valid_from = self.effective_from
            new.is_default = old.is_default
            new.is_active = True
            new.save()
            moved = WorkOrder.objects.filter(bom=old, status=WorkOrderStatus.DRAFT).filter(
                Q(scheduled_start__isnull=True) | Q(scheduled_start__gte=self.effective_from),
            ).update(bom=new, updated_at=timezone.now())
            self.number = DocumentSequence.next_for(
                "manufacturing.bom_change", self.effective_from, name="BOM change orders", prefix="ECO-")
            said = f"{moved} draft run{'s' if moved != 1 else ''} moved to version {new.version}."
            self._decide(ChangeStatus.APPLIED, by, f"{note.strip()} {said}".strip())

    @serialised("status")
    def reject(self, by=None, note=""):
        """Not done: the draft version is put out of use, and the old one stands as it was."""
        if self.status != ChangeStatus.DRAFT:
            raise ValidationError(f"This change is already {self.get_status_display().lower()}.")
        if not note.strip():
            raise ValidationError({"note": ["Say why, or the same change comes back unchanged."]})
        with transaction.atomic():
            self.draft.is_active = False
            self.draft.save()
            self._decide(ChangeStatus.REJECTED, by, note)


def raise_change(bom, effective_from, reason, by=None):
    """
    A new version of `bom`, copied line for line to be edited, and the
    order that will put it in force from `effective_from`. One open
    change per version; a computed bill is changed at its specification.
    """
    effective_from = to_date(effective_from)
    if effective_from is None:
        raise ValidationError({"effective_from": ["Say the first day output is made to the new version."]})
    if not (reason or "").strip():
        raise ValidationError({"reason": ["Say what changes and why."]})
    if bom.is_computed:
        raise ValidationError(
            f"{bom} is computed from {bom.computed_by() or 'a specification'}; change that, and this bill "
            "is rebuilt from it.")
    if not bom.is_active:
        raise ValidationError(f"{bom} is out of use; raise the change on the version in use.")
    if BomChangeOrder.objects.filter(supersedes=bom, status=ChangeStatus.DRAFT).exists():
        raise ValidationError(f"{bom} already has a change order open; apply or reject that one first.")
    if bom.valid_from is not None and effective_from <= bom.valid_from:
        raise ValidationError({"effective_from": [
            f"{bom} itself starts on {bom.valid_from:%d %b %Y}; a change to it takes effect after that."]})
    if bom.valid_to is not None and effective_from > bom.valid_to + datetime.timedelta(days=1):
        raise ValidationError({"effective_from": [
            f"{bom} ends on {bom.valid_to:%d %b %Y}; the change takes effect by the day after, "
            "or the days between have no recipe."]})
    with transaction.atomic():
        version = (bom.item.boms.aggregate(top=models.Max("version"))["top"] or 0) + 1
        new = BillOfMaterials.objects.create(
            item=bom.item, version=version, name=bom.name, is_rework=bom.is_rework, backflush=bom.backflush,
            quantity_produced=bom.quantity_produced, uom=bom.uom, routing=bom.routing, is_default=False,
            valid_from=effective_from, valid_to=bom.valid_to, is_phantom=bom.is_phantom,
            expected_reject_percent=bom.expected_reject_percent, is_active=True, notes=bom.notes,
        )
        copies = {}
        for component in bom.components.all():
            copies[component.pk] = BomComponent.objects.create(
                bom=new, item=component.item, quantity=component.quantity, uom=component.uom,
                waste_percent=component.waste_percent, line_number=component.line_number, notes=component.notes)
        for byproduct in bom.byproducts.all():
            BomByproduct.objects.create(
                bom=new, item=byproduct.item, quantity=byproduct.quantity, uom=byproduct.uom,
                valuation=byproduct.valuation, cost_share_percent=byproduct.cost_share_percent,
                line_number=byproduct.line_number)
        for substitute in BomSubstitute.objects.filter(component__bom=bom):
            BomSubstitute.objects.create(
                component=copies[substitute.component_id], item=substitute.item, quantity_per=substitute.quantity_per,
                priority=substitute.priority, is_active=substitute.is_active, notes=substitute.notes)
        return BomChangeOrder.objects.create(supersedes=bom, draft=new, effective_from=effective_from,
                                             reason=reason.strip(), created_by=by)
