"""
Files on a record over the API: ?model=sales.delivery&id=7 lists them,
a multipart POST with model, id and file keeps one, {id}/download/
serves it, DELETE removes it. Reading and attaching take the record's
own view permission (and core.add_attachment to attach); removing takes
core.delete_attachment, or having attached it oneself.
"""

from django.apps import apps
from django.http import FileResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .api import record_or_404
from .attachments import Attachment, attach, attachments_of, detach
from .endpoints import may_read


def _record(request, data):
    """The record a request names by model and id, once the login may read its kind."""
    label, pk = str(data.get("model", "")), str(data.get("id", ""))
    try:
        model = apps.get_model(label)
    except (LookupError, ValueError):
        raise ValidationError({"model": ["Name the record's kind as app.model."]})
    if not request.user.has_perm(f"{model._meta.app_label}.view_{model._meta.model_name}"):
        raise PermissionDenied(f"Reading {model._meta.verbose_name_plural} is not yours.")
    kept = record_or_404(model, pk, "id")
    if not may_read(request.user, model, kept.pk):
        raise NotFound()  # as its own screen answers: one the login may not see is not there
    return kept


def _row(attachment, user):
    return {
        "id": attachment.pk, "name": attachment.name, "size": attachment.size, "media_type": attachment.media_type,
        "by": attachment.created_by.get_username() if attachment.created_by_id else "", "at": attachment.created_at,
        "mine": attachment.created_by_id == user.pk,
    }


class AttachmentViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        kept = _record(request, request.query_params)
        return Response([_row(row, request.user) for row in attachments_of(type(kept), kept.pk)])

    def create(self, request):
        if not request.user.has_perm("core.add_attachment"):
            raise PermissionDenied("Attaching files is not yours.")
        kept = _record(request, request.data)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"file": ["Choose a file."]})
        return Response(_row(attach(kept, uploaded, request.user), request.user), status=201)

    def _attachment(self, request, pk):
        attachment = record_or_404(Attachment, pk, "id")
        model = attachment.content_type.model_class()
        if model is None or not request.user.has_perm(f"{model._meta.app_label}.view_{model._meta.model_name}"):
            raise PermissionDenied("Reading this record is not yours.")
        if not may_read(request.user, model, attachment.object_id):
            raise NotFound()
        return attachment

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        attachment = self._attachment(request, pk)
        try:
            handle = attachment.file.open("rb")
        except FileNotFoundError:
            raise NotFound(f"{attachment.name} is no longer on the server's disk.")
        return FileResponse(handle, as_attachment=True, filename=attachment.name, content_type=attachment.media_type)

    def destroy(self, request, pk=None):
        attachment = self._attachment(request, pk)
        if attachment.created_by_id != request.user.pk and not request.user.has_perm("core.delete_attachment"):
            raise PermissionDenied("Only whoever attached it, or someone who may remove attachments, removes it.")
        detach(attachment, request.user)
        return Response(status=204)
