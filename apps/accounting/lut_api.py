"""
The Letter of Undertaking and what setup_indian_books seeded, kept from the office (O239): both had the
admin only. An LUT is recorded under the registration in force; what was seeded is listed for the
accountant to review and marked reviewed, by whom and when.
"""

from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .gst import GstSettings, LetterOfUndertaking
from .tcs import SeededDefault


class LetterOfUndertakingSerializer(serializers.ModelSerializer):
    registration = serializers.PrimaryKeyRelatedField(queryset=GstSettings.objects.all(), required=False)

    class Meta:
        model = LetterOfUndertaking
        fields = ["id", "registration", "arn", "financial_year", "filed_on", "valid_from", "valid_to"]

    def to_internal_value(self, data):
        # Filed under the registration in force unless another is named; given before the
        # one-ARN-per-registration check reads it.
        if self.instance is None and not data.get("registration"):
            registration = GstSettings.active()
            if registration is None:
                raise DRFValidationError({"registration": ["There is no GST registration in force to file it under."]})
            data = {**data, "registration": registration.pk}
        return super().to_internal_value(data)


class LetterOfUndertakingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = LetterOfUndertaking.objects.select_related("registration")
    serializer_class = LetterOfUndertakingSerializer
    filter_fields = ["financial_year"]
    search_fields = ["arn", "financial_year"]
    date_field = "valid_from"


class SeededDefaultSerializer(serializers.ModelSerializer):
    reviewed_by_name = serializers.CharField(source="reviewed_by.username", read_only=True, default=None)

    class Meta:
        model = SeededDefault
        fields = ["id", "label", "model", "object_id", "reviewed_by", "reviewed_by_name", "reviewed_on"]
        read_only_fields = fields


class SeededDefaultViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = SeededDefault.objects.select_related("reviewed_by")
    serializer_class = SeededDefaultSerializer
    filter_fields = ["model"]
    search_fields = ["label", "model"]
    action_permission_map = {"review": "accounting.change_seededdefault"}

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        """Mark it reviewed, by the person asking, today."""
        row = self.get_object()
        if row.reviewed_on:
            raise DRFValidationError([f"{row.label} was reviewed on {row.reviewed_on:%d %b %Y}."])
        row.reviewed_by, row.reviewed_on = request.user, timezone.localdate()
        row.save(update_fields=["reviewed_by", "reviewed_on", "updated_at"])
        return Response(self.get_serializer(row).data)
