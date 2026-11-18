"""Where the manufacturing side of the ledger lands: one record, kept by the controller."""

from rest_framework import viewsets

from apps.core.api import ModelSerializer

from apps.core.audit import AuditableViewSetMixin

from .orders import ManufacturingSettings


class ManufacturingSettingsSerializer(ModelSerializer):
    class Meta:
        model = ManufacturingSettings
        exclude = ["created_at", "updated_at", "created_by", "updated_by"]


class ManufacturingSettingsViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ManufacturingSettingsSerializer
    http_method_names = ["get", "patch", "put", "head", "options"]

    def get_queryset(self):
        # The one record, made on first sight as the code that reads it does.
        ManufacturingSettings.get()
        return ManufacturingSettings.objects.all()
