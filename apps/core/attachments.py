"""
Files kept with a record: the vendor's PO copy on a purchase order, the
LR scan on a delivery, a test certificate on a lot. One table for every
kind of record, the file on disk under MEDIA_ROOT, served only through
the API under the record's own view permission, never by URL.

What may be attached is a short list of kinds and a size; anything else
is refused in words rather than kept and served to a browser.
"""

import os
import uuid

from django.core.exceptions import ValidationError
from django.db import models

from .history import EventKind, record
from .models import AuditModel

ALLOWED = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel", ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}
MAX_BYTES = 20 * 1024 * 1024


def check_file(name, size):
    """The kind and size an attachment may be; refused in words otherwise."""
    extension = os.path.splitext(name or "")[1].lower()
    if extension not in ALLOWED:
        raise ValidationError({"file": f"{name!r} is not a kind of file that is kept: "
                                       f"{', '.join(sorted(ALLOWED))}."})
    if not size:
        raise ValidationError({"file": f"{name!r} is empty."})
    if size > MAX_BYTES:
        raise ValidationError({"file": f"{name!r} is {size / 1024 / 1024:.1f} MB; up to {MAX_BYTES // 1024 // 1024} MB is kept."})
    return ALLOWED[extension]


def _path(attachment, filename):
    kind = attachment.content_type
    return f"attachments/{kind.app_label}/{kind.model}/{attachment.object_id}/{uuid.uuid4().hex}{os.path.splitext(filename)[1].lower()}"


class Attachment(AuditModel):
    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    file = models.FileField(upload_to=_path, max_length=255)
    name = models.CharField(max_length=255, help_text="As the person named it.")
    size = models.PositiveBigIntegerField()
    media_type = models.CharField(max_length=100)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"], name="attachment_by_record")]
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.media_type = check_file(self.name, self.size)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.file.delete(save=False)
        return super().delete(*args, **kwargs)


def attach(instance, uploaded, user):
    """Keep an uploaded file with the record, and say so in its history."""
    from django.contrib.contenttypes.models import ContentType

    attachment = Attachment(content_type=ContentType.objects.get_for_model(type(instance)), object_id=instance.pk,
                            name=os.path.basename(uploaded.name or ""), size=uploaded.size,
                            created_by=user, updated_by=user)
    check_file(attachment.name, attachment.size)
    attachment.file = uploaded
    attachment.save()
    record(instance, user, EventKind.ACTION, action="attach", summary=attachment.name)
    return attachment


def detach(attachment, user):
    """Remove a file from its record, and say so in the record's history."""
    model = attachment.content_type.model_class()
    kept = model.objects.filter(pk=attachment.object_id).first()
    name = attachment.name
    attachment.delete()
    if kept is not None:
        record(kept, user, EventKind.ACTION, action="detach", summary=name)


def attachments_of(model, pk):
    from django.contrib.contenttypes.models import ContentType

    return Attachment.objects.filter(content_type=ContentType.objects.get_for_model(model), object_id=pk) \
        .select_related("created_by")
