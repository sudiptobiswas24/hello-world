"""A record's history, for its page: ?model=sales.delivery&id=7, read under the record's own view permission."""

from django.apps import apps
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .history import history


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
        return Response(history(model, int(pk)))
