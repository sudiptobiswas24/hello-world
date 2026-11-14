"""
Files kept with a record: the vendor's PO copy on a purchase order, the
LR scan on a delivery, a test certificate on a lot. One table for every
kind of record, the file on disk under MEDIA_ROOT, served only through
the API under the record's own view permission, never by URL.

What may be attached is a short list of kinds and a size; anything else
is refused in words rather than kept and served to a browser.

**A file kept for a purpose.** Most files are simply with their record.
Some are one particular paper of it: the signed copy of a delivery is
its proof of delivery. Such a file names its purpose, and the module
that owns the record registers what the purpose takes
(register_purpose): which kind of record, which kinds of file, and who
may keep one on which record. A purpose is never edited afterwards and
the paper is never replaced in place: a better scan is added, the latest
is the one in force (current), and the earlier ones stay listed under it.
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


# How a file of each kind begins. A purpose's paper is read as well as named: a scan is
# opened in a browser by the people who argue a delivery from it, and a name is only a name.
BEGINS = {".pdf": (b"%PDF-",), ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",),
          ".jpeg": (b"\xff\xd8\xff",)}


class Purpose:
    """What one named paper of a record takes; `check(instance, user)` raises where it may not be kept."""

    def __init__(self, code, label, model, extensions, check):
        unread = sorted(set(extensions) - set(BEGINS))
        if unread or not set(extensions) <= set(ALLOWED):
            raise ValueError(f"{code}: no rule here reads the content of {', '.join(unread)}.")
        self.code, self.label, self.model, self.extensions, self.check = (
            code, label, model.lower(), tuple(sorted(extensions)), check)


PURPOSES = {}


def register_purpose(code, *, label, model, extensions, check):
    """Called from the owning module's AppConfig.ready(): core names no module's papers itself."""
    PURPOSES[code] = Purpose(code, label, model, extensions, check)


def purpose_of(code):
    """The registered purpose, None for a file that has none; an unknown one is refused."""
    if not code:
        return None
    if code not in PURPOSES:
        raise ValidationError({"purpose": f"{code!r} is not a purpose a file is kept for: "
                                          f"{', '.join(sorted(PURPOSES)) or 'none is set up'}."})
    return PURPOSES[code]


def purpose_label(code):
    return PURPOSES[code].label if code in PURPOSES else code


def check_content(name, handle):
    """That a purpose's file begins as its name says it should."""
    extension = os.path.splitext(name or "")[1].lower()
    handle.seek(0)
    head = handle.read(16)
    handle.seek(0)
    if not any(head.startswith(mark) for mark in BEGINS[extension]):
        raise ValidationError({"file": f"{name!r} is named {extension} and is not one inside: "
                                       "scan or photograph it again, or save it as what it is."})


def check_file(name, size, purpose=""):
    """The kind and size an attachment may be; refused in words otherwise."""
    extension = os.path.splitext(name or "")[1].lower()
    kept_for = purpose_of(purpose)
    if kept_for is not None and extension not in kept_for.extensions:
        raise ValidationError({"file": f"A {kept_for.label.lower()} is kept as {', '.join(kept_for.extensions)}; "
                                       f"{name!r} is not."})
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
    purpose = models.CharField(
        max_length=32, blank=True, default="", editable=False,
        help_text="Which paper of the record this is (proof_of_delivery), where it is one; set when it is "
                  "attached and never changed. The latest of a purpose is the one in force.")

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"], name="attachment_by_record")]
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.media_type = check_file(self.name, self.size, self.purpose)
        if self._state.adding:
            if self.purpose:
                kind = f"{self.content_type.app_label}.{self.content_type.model}"
                if kind != PURPOSES[self.purpose].model:
                    raise ValidationError({"purpose": f"A {purpose_label(self.purpose).lower()} is kept on a "
                                                      f"{PURPOSES[self.purpose].model}, not a {kind}."})
                check_content(self.name, self.file)
        elif Attachment.objects.filter(pk=self.pk).exclude(purpose=self.purpose).exists():
            # The edit path: a file does not become, or stop being, a record's signed paper afterwards.
            raise ValidationError({"purpose": "What a file was kept as is not changed; add the right one "
                                              "and remove this."})
        super().save(*args, **kwargs)

    @property
    def said(self):
        """As the record's history names it."""
        return f"{purpose_label(self.purpose)}: {self.name}" if self.purpose else self.name

    def delete(self, *args, **kwargs):
        self.file.delete(save=False)
        return super().delete(*args, **kwargs)


def attach(instance, uploaded, user, purpose=""):
    """
    Keep an uploaded file with the record, and say so in its history. With
    a purpose, as that paper of the record: the purpose's own rule is asked
    first (the kind of record, then who may keep one on this record).
    """
    from django.contrib.contenttypes.models import ContentType

    kept_for = purpose_of(purpose)
    if kept_for is not None:
        if type(instance)._meta.label_lower != kept_for.model:
            raise ValidationError({"purpose": f"A {kept_for.label.lower()} is kept on a {kept_for.model}, "
                                              f"not a {type(instance)._meta.label_lower}."})
        kept_for.check(instance, user)
    attachment = Attachment(content_type=ContentType.objects.get_for_model(type(instance)), object_id=instance.pk,
                            name=os.path.basename(uploaded.name or ""), size=uploaded.size, purpose=purpose or "",
                            created_by=user, updated_by=user)
    check_file(attachment.name, attachment.size, attachment.purpose)
    attachment.file = uploaded
    attachment.save()
    record(instance, user, EventKind.ACTION, action="attach", summary=attachment.said)
    return attachment


def detach(attachment, user):
    """Remove a file from its record, and say so in the record's history."""
    model = attachment.content_type.model_class()
    kept = model.objects.filter(pk=attachment.object_id).first()
    name = attachment.said
    attachment.delete()
    if kept is not None:
        record(kept, user, EventKind.ACTION, action="detach", summary=name)


def attachments_of(model, pk):
    from django.contrib.contenttypes.models import ContentType

    return Attachment.objects.filter(content_type=ContentType.objects.get_for_model(model), object_id=pk) \
        .select_related("created_by")
