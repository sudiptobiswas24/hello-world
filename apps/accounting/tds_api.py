"""TDS and TCS sections: the rates and thresholds the plant deducts, collects and is deducted at, kept by the controller."""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .tcs import TcsDeclaration, TcsRate, TcsSection
from .tds import TdsRate, TdsSection


class TdsSectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TdsSection
        fields = ["id", "code", "name", "section_1961", "section_2025", "mode", "single_threshold",
                  "annual_threshold", "payable_account", "receivable_account", "is_active"]


class TdsSectionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TdsSection.objects.all()
    serializer_class = TdsSectionSerializer
    filter_fields = ["is_active", "mode"]
    search_fields = ["code", "name"]


class TcsSectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TcsSection
        fields = ["id", "code", "name", "section_1961", "section_2025", "form_code", "base_includes_gst",
                  "payable_account", "is_active"]


class TcsSectionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TcsSection.objects.all()
    serializer_class = TcsSectionSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]


class TcsRateSerializer(serializers.ModelSerializer):
    section_code = serializers.CharField(source="section.code", read_only=True)
    law_section = serializers.CharField(source="section_code", required=False, allow_blank=True, max_length=16)

    class Meta:
        model = TcsRate
        fields = ["id", "section", "section_code", "applies_from", "rate_percent", "no_pan_rate_percent",
                  "authority", "law_section", "confirmed_by", "confirmed_on"]
        read_only_fields = ["confirmed_by", "confirmed_on"]


class TcsRateViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """A rate the law sets, from the day it applies; confirm/ is the accountant's word that it is the law's."""

    queryset = TcsRate.objects.select_related("section")
    serializer_class = TcsRateSerializer
    filter_fields = ["section"]
    search_fields = ["section__code", "authority"]
    date_field = "applies_from"
    action_permission_map = {"confirm": "accounting.confirm_tcsrate"}

    # The request's names for the figures a confirmation vouches for (DatedRate.FIGURES).
    READ_AS = {"section_id": "section", "section_code": "law_section"}

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """{section, applies_from, rate_percent, no_pan_rate_percent[, law_section]}: the figures read."""
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError as DRFValidationError

        rate = self.get_object()
        seen = {name: request.data.get(self.READ_AS.get(name, name)) for name in rate.FIGURES}
        try:
            rate.confirm(request.user, seen=seen)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(type(rate).objects.get(pk=rate.pk)).data)


class TdsRateSerializer(serializers.ModelSerializer):
    section_code = serializers.CharField(source="section.code", read_only=True)

    class Meta:
        model = TdsRate
        fields = ["id", "section", "section_code", "applies_from", "rate_percent", "no_pan_rate_percent",
                  "authority", "confirmed_by", "confirmed_on"]
        read_only_fields = ["confirmed_by", "confirmed_on"]


class TdsRateViewSet(TcsRateViewSet):
    """A TDS rate the law sets, from the day it applies: the TCS rate's screen and rules (accounting.dated_rates)."""

    queryset = TdsRate.objects.select_related("section")
    serializer_class = TdsRateSerializer
    action_permission_map = {"confirm": "accounting.confirm_tdsrate"}


class TcsDeclarationSerializer(serializers.ModelSerializer):
    party_name = serializers.CharField(source="party.name", read_only=True)

    class Meta:
        model = TcsDeclaration
        fields = ["id", "party", "party_name", "section", "form", "pan", "reference", "received_on", "valid_from",
                  "valid_to"]
        read_only_fields = ["form"]


class TcsDeclarationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """The buyer's declaration that its purchases are for its own manufacture; its form follows its date."""

    queryset = TcsDeclaration.objects.select_related("party", "section")
    serializer_class = TcsDeclarationSerializer
    filter_fields = ["party", "section"]
    search_fields = ["party__name", "reference"]
    date_field = "valid_from"
