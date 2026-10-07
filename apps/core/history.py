"""
What happened to a record, by whom and when: made, changed (which
fields), each action taken on it (posted, voided, approved, sent), each
mail that went out about it, deleted. One table for every kind of
record, written by the API layer (apps/core/audit.py) so nothing has to
remember to write it, and read on the record's own page.

It is the API's history. A change made in the Django admin or by a
script leaves `updated_by` and `updated_at` on the record and nothing
here (docs/RISKS.md).
"""

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class EventKind(models.TextChoices):
    CREATED = "created", "Made"
    UPDATED = "updated", "Changed"
    ACTION = "action", "Done"
    MAIL = "mail", "Sent"
    DELETED = "deleted", "Deleted"


class RecordEvent(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    at = models.DateTimeField(default=timezone.now, editable=False)
    who = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="+")
    kind = models.CharField(max_length=8, choices=EventKind.choices)
    action = models.CharField(max_length=64, blank=True, help_text="The action's name: post, void, approve, send.")
    summary = models.CharField(max_length=255, blank=True,
                               help_text="The fields a change touched, or who a mail went to.")

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id", "-at"], name="record_event_by_record")]
        ordering = ["-at", "-pk"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.content_type.model} {self.object_id}"


def record(instance, user, kind, action="", summary=""):
    """One line in the record's history. `instance` may be gone from the database (a delete) but still has its pk."""
    return RecordEvent.objects.create(
        content_type=ContentType.objects.get_for_model(type(instance)), object_id=instance.pk,
        who=user if getattr(user, "pk", None) else None, kind=kind, action=action, summary=summary[:255])


def record_by_id(model, pk, user, kind, action="", summary=""):
    """The same, for a view that knows the record's model and id and has not loaded it."""
    return RecordEvent.objects.create(
        content_type=ContentType.objects.get_for_model(model), object_id=pk,
        who=user if getattr(user, "pk", None) else None, kind=kind, action=action, summary=summary[:255])


def words_for(action):
    """An action's name as the history reads it: post_delivery -> Posted delivery; send -> Sent."""
    head, _, rest = action.partition("_")
    past = {"post": "Posted", "void": "Voided", "send": "Sent", "approve": "Approved", "confirm": "Confirmed",
            "cancel": "Cancelled", "reverse": "Reversed", "close": "Closed", "reopen": "Reopened",
            "accept": "Accepted", "reject": "Rejected", "release": "Released", "receive": "Received",
            "ship": "Shipped", "pay": "Paid", "allocate": "Allocated", "break": "Broken", "load": "Loaded",
            "unload": "Unloaded", "convert": "Converted", "lose": "Lost", "win": "Won", "take": "Taken",
            "quote": "Quoted", "done": "Done", "renew": "Renewed", "settle": "Settled", "withdraw": "Withdrawn",
            "submit": "Submitted", "issue": "Issued", "return": "Returned", "mark": "Marked"}
    verb = past.get(head.rstrip("_"), head.replace("_", " ").capitalize())
    return f"{verb} {rest.replace('_', ' ')}".strip()


def history(model, pk):
    """[{at, who, kind, action, summary}] newest first, for the record's page."""
    rows = RecordEvent.objects.filter(
        content_type=ContentType.objects.get_for_model(model), object_id=pk).select_related("who")
    return [{
        "id": event.pk, "at": event.at, "who": event.who.get_username() if event.who_id else "",
        "kind": event.kind, "label": words_for(event.action) if event.kind == EventKind.ACTION else event.get_kind_display(),
        "summary": event.summary,
    } for event in rows]
