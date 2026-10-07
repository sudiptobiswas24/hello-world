"""
What people say and plan about a record, kept on it: notes, and
follow-ups (a call, a visit, a document to chase) with a day and a
person. One table each for every kind of record, beside its history and
its files, and read under the same rule: whoever may read the record.

A note is what was said: kept as written, removed only by whoever wrote
it. A follow-up is planned, rescheduled or handed on until it is done;
done, it is a fact, and says so in a note on the record. One marked done
by mistake is not reopened: another is planned.
"""

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .endpoints import may_read
from .models import AuditModel, serialised


class Note(AuditModel):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    body = models.TextField()

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id", "-created_at"], name="note_by_record")]
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.body[:60]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("A note is kept as it was written; remove it and write another.")
        self.body = self.body.strip()
        if not self.body:
            raise ValidationError({"body": ["Say something."]})
        super().save(*args, **kwargs)


class FollowUpKind(models.TextChoices):
    CALL = "call", "Call"
    VISIT = "visit", "Visit or meeting"
    EMAIL = "email", "Email or message"
    DOCUMENT = "document", "Document to get"
    TODO = "todo", "To do"


class FollowUp(AuditModel):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=16, choices=FollowUpKind.choices, default=FollowUpKind.TODO)
    summary = models.CharField(max_length=255)
    note = models.TextField(blank=True)
    due_on = models.DateField()
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="follow_ups")
    # Where the record is opened in the office application, so a list of
    # someone's follow-ups leads to each record. Written by the screen it
    # was planned on; only a path within the application is kept.
    link = models.CharField(max_length=255, blank=True)
    done_on = models.DateField(null=True, blank=True)
    done_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    outcome = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"], name="follow_up_by_record"),
                   models.Index(fields=["assigned_to", "done_on", "due_on"], name="follow_up_by_person")]
        ordering = ["due_on", "pk"]
        constraints = [
            models.CheckConstraint(check=Q(done_on__isnull=True) | Q(done_by__isnull=False),
                                   name="follow_up_done_by_someone"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.summary}"

    @property
    def record_model(self):
        return self.content_type.model_class()

    def save(self, *args, **kwargs):
        before = None if self._state.adding else FollowUp.objects.filter(pk=self.pk).values(
            "done_on", "assigned_to_id").first()
        if before and before["done_on"] is not None:
            raise ValidationError("This follow-up is done; plan another rather than change what was done.")
        self.summary = self.summary.strip()
        if not self.summary:
            raise ValidationError({"summary": ["Say what is to be done."]})
        if self.link and (not self.link.startswith("/") or self.link.startswith("//")):
            raise ValidationError({"link": ["A place in the application, starting with /."]})
        # Asked when it is planned or handed on: whoever it is for must be
        # able to open the record, or it would sit on a list they cannot act on.
        if (before is None or before["assigned_to_id"] != self.assigned_to_id) and self.done_on is None:
            person = self.assigned_to
            if not person.is_active:
                raise ValidationError({"assigned_to": [f"{person.get_username()} no longer signs in."]})
            model = self.record_model
            if model is None or not may_read(person, model, self.object_id):
                raise ValidationError({"assigned_to": [f"{person.get_username()} cannot open this record, "
                                                       "so cannot follow it up."]})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if FollowUp.objects.filter(pk=self.pk, done_on__isnull=False).exists():
            raise ValidationError("A follow-up that is done is part of the record's story and stays.")
        return super().delete(*args, **kwargs)

    @serialised("done_on")
    def mark_done(self, user, outcome="", on_date=None):
        """Done: by whom, when and how it went, and a note on the record saying so."""
        if self.done_on is not None:
            raise ValidationError(f"Done already, on {self.done_on:%d %b %Y}.")
        self.done_on = on_date or timezone.localdate()
        self.done_by = user
        self.outcome = outcome.strip()
        FollowUp.objects.filter(pk=self.pk).update(done_on=self.done_on, done_by=user, outcome=self.outcome,
                                                   updated_by=user)
        said = f"{self.get_kind_display()} done: {self.summary}"
        Note.objects.create(content_type=self.content_type, object_id=self.object_id,
                            body=f"{said}. {self.outcome}" if self.outcome else said,
                            created_by=user, updated_by=user)
        return self
