"""The one quality policy that is a choice rather than a rule, kept by the quality manager."""

from rest_framework import serializers, viewsets

from apps.core.audit import AuditableViewSetMixin

from .models import QualitySettings


class QualitySettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = QualitySettings
        exclude = ["created_at", "updated_at", "created_by", "updated_by"]


class QualitySettingsViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    serializer_class = QualitySettingsSerializer
    http_method_names = ["get", "patch", "put", "head", "options"]

    def get_queryset(self):
        QualitySettings.get()
        return QualitySettings.objects.all()
