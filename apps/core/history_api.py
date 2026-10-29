"""
A record's history, for its page: ?model=sales.delivery&id=7, read under the record's own view permission. And
the edit log, every record's changes, for whoever reads it (core.view_recordevent).
"""

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .endpoints import may_read, may_read_gone
from .history import RecordEvent, as_row, changes_for, history


class HistoryView(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        label, pk = request.query_params.get("model", ""), request.query_params.get("id", "")
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError):
            raise ValidationError({"model": ["Name the record's kind as app.model."]})
        if not pk.isdigit():
            raise ValidationError({"id": ["Name the record by its id."]})
        if not request.user.has_perm(f"{model._meta.app_label}.view_{model._meta.model_name}"):
            raise PermissionDenied(f"Reading {model._meta.verbose_name_plural} is not yours.")
        # Not only the kind but the record: a rep reads the history of their own customers' orders.
        # A deleted record's only by whoever reads every one of its kind (endpoints.may_read_gone).
        if model._default_manager.filter(pk=int(pk)).exists():
            if not may_read(request.user, model, int(pk)):
                raise NotFound()
        elif not may_read_gone(request.user, model):
            raise NotFound()
        return Response(history(model, int(pk)))


class EditLogSerializer(serializers.ModelSerializer):
    """One change, as the edit log lists it: when, who, through what, which record, each field's old and new."""

    record = serializers.SerializerMethodField()
    model = serializers.SerializerMethodField()
    who_name = serializers.SerializerMethodField()
    label = serializers.SerializerMethodField()
    changes = serializers.SerializerMethodField()

    class Meta:
        model = RecordEvent
        fields = ["id", "at", "who", "who_name", "through", "kind", "label", "action", "model", "object_id", "record",
                  "summary", "changes"]
        read_only_fields = fields

    def get_model(self, event):
        return f"{event.content_type.app_label}.{event.content_type.model}"

    def get_record(self, event):
        model = event.content_type.model_class()
        return f"{model._meta.verbose_name if model else event.content_type.model} {event.object_id}"

    def get_who_name(self, event):
        return event.who.get_username() if event.who_id else event.who_name

    def get_label(self, event):
        return as_row(event)["label"]

    def get_changes(self, event):
        return changes_for(event)


class EditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    The edit log: every change to every record, in a date range (?from=&to=), by kind of record
    (?model=sales.invoice) and by person (?who=<id>), for the Controller and the auditor. Read only, and
    nothing in it is ever removed (apps/core/history.py). Read under core.view_recordevent.
    """

    queryset = RecordEvent.objects.select_related("who", "content_type")
    serializer_class = EditLogSerializer
    filter_fields = ["kind", "through", "who", "object_id"]
    search_fields = ["summary", "who_name", "through", "content_type__model"]
    extra_params = ["model"]
    date_field = "at"
    ordering_fields = ["at"]

    def get_queryset(self):
        rows = super().get_queryset()
        user = self.request.user
        if not user.is_superuser:
            # A record's values only to whoever may read that kind of record in place: the Controller sees pay
            # (it posts the runs), a login given the log and no pay right sees no pay change through it.
            held = user.get_all_permissions()
            rows = rows.filter(content_type_id__in=[
                kind.pk for kind in ContentType.objects.all() if f"{kind.app_label}.view_{kind.model}" in held])
        label = self.request.query_params.get("model", "")
        if label:
            try:
                model = apps.get_model(label)
            except (LookupError, ValueError):
                raise ValidationError({"model": ["Name the record's kind as app.model."]})
            rows = rows.filter(content_type=ContentType.objects.get_for_model(model))
        return rows
