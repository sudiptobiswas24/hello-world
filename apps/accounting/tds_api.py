"""TDS sections: the rates and thresholds the plant deducts and is deducted at, kept by the controller."""

from rest_framework import serializers, viewsets

from apps.core.audit import AuditableViewSetMixin

from .tds import TdsSection


class TdsSectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TdsSection
        fields = ["id", "code", "name", "rate_percent", "no_pan_rate_percent", "mode", "single_threshold",
                  "annual_threshold", "payable_account", "receivable_account", "is_active"]


class TdsSectionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TdsSection.objects.all()
    serializer_class = TdsSectionSerializer
    filter_fields = ["is_active", "mode"]
    search_fields = ["code", "name"]
