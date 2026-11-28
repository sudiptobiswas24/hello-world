"""TDS and TCS sections: the rates and thresholds the plant deducts, collects and is deducted at, kept by the controller."""

from rest_framework import serializers, viewsets

from apps.core.api import ModelSerializer, takes_only
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .bank_rate import BankRate
from .tcs import TcsCertificate, TcsDeclaration, TcsRate, TcsSection
from .tds import TdsRate, TdsSection


class TdsSectionSerializer(ModelSerializer):
    class Meta:
        model = TdsSection
        fields = ["id", "code", "name", "section_1961", "section_2025", "mode", "single_threshold",
                  "annual_threshold", "payable_account", "receivable_account", "is_active"]


class TdsSectionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TdsSection.objects.all()
    serializer_class = TdsSectionSerializer
    filter_fields = ["is_active", "mode"]
    search_fields = ["code", "name"]


class TcsSectionSerializer(ModelSerializer):
    class Meta:
        model = TcsSection
        fields = ["id", "code", "name", "section_1961", "section_2025", "form_code", "base_includes_gst",
                  "payable_account", "is_active"]


class TcsSectionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TcsSection.objects.all()
    serializer_class = TcsSectionSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]


class KeptBecauseMixin(serializers.Serializer):
    # Why it may not be deleted, or "": the screen offers Delete only where the server would take it (O556).
    # Read from what the list asked of every row at once (DatedRate.with_taken, O568), not one EXISTS a row. Every
    # dated rate's serializer takes it: TDS, TCS and the bank rate (O589).
    kept_because = serializers.SerializerMethodField()

    def get_kept_because(self, rate) -> str:
        return rate.kept_because(taken=getattr(rate, "was_taken", None))


class TcsRateSerializer(KeptBecauseMixin, ModelSerializer):
    section_code = serializers.CharField(source="section.code", read_only=True)
    law_section = serializers.CharField(source="section_code", required=False, allow_blank=True, max_length=16)

    class Meta:
        model = TcsRate
        fields = ["id", "section", "section_code", "applies_from", "rate_percent", "no_pan_rate_percent",
                  "authority", "law_section", "confirmed_by", "confirmed_on", "kept_because"]
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

    def get_queryset(self):
        # Whether each row was taken, in the list's one query (O568).
        return self.queryset.model.with_taken(super().get_queryset())

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """{section, applies_from, rate_percent, no_pan_rate_percent[, law_section]}: the figures read."""
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError as DRFValidationError

        rate = self.get_object()
        # The figures it confirms are the keys it takes (O579): any other is refused, not dropped.
        takes_only(request.data, *(self.READ_AS.get(name, name) for name in rate.FIGURES))
        seen = {name: request.data.get(self.READ_AS.get(name, name)) for name in rate.FIGURES}
        try:
            rate.confirm(request.user, seen=seen)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(type(rate).objects.get(pk=rate.pk)).data)


class TdsRateSerializer(KeptBecauseMixin, ModelSerializer):
    section_code = serializers.CharField(source="section.code", read_only=True)

    class Meta:
        model = TdsRate
        fields = ["id", "section", "section_code", "applies_from", "rate_percent", "no_pan_rate_percent",
                  "authority", "confirmed_by", "confirmed_on", "kept_because"]
        read_only_fields = ["confirmed_by", "confirmed_on"]


class TdsRateViewSet(TcsRateViewSet):
    """A TDS rate the law sets, from the day it applies: the TCS rate's screen and rules (accounting.dated_rates)."""

    queryset = TdsRate.objects.select_related("section")
    serializer_class = TdsRateSerializer
    action_permission_map = {"confirm": "accounting.confirm_tdsrate"}


class BankRateSerializer(KeptBecauseMixin, ModelSerializer):
    # kept_because from the mixin the TDS and TCS rates share (O589): a bank rate is taken by nothing, so with_taken
    # leaves its list as it is and the words are read off the row (BankRate.kept_because).
    class Meta:
        model = BankRate
        fields = ["id", "applies_from", "rate_percent", "authority", "confirmed_by", "confirmed_on", "kept_because"]
        read_only_fields = ["confirmed_by", "confirmed_on"]


class BankRateViewSet(TcsRateViewSet):
    """The Reserve Bank's bank rate from a day (MSMED Act s.16): the dated rate's screen and rules."""

    queryset = BankRate.objects.all()
    serializer_class = BankRateSerializer
    filter_fields = []
    search_fields = ["authority"]
    action_permission_map = {"confirm": "accounting.confirm_bankrate"}


class TcsDeclarationSerializer(ModelSerializer):
    party_name = serializers.CharField(source="party.name", read_only=True)

    class Meta:
        model = TcsDeclaration
        fields = ["id", "party", "party_name", "section", "form", "pan", "reference", "received_on", "valid_from",
                  "valid_to"]
        read_only_fields = ["form"]


class TcsCertificateSerializer(ModelSerializer):
    party_name = serializers.CharField(source="party.name", read_only=True)
    # What is left on its amount today: the part at its rate of the invoices collected on under it, less what
    # credit notes gave back of them (O619).
    left = serializers.SerializerMethodField()

    class Meta:
        model = TcsCertificate
        fields = ["id", "party", "party_name", "section", "reference", "rate_percent", "valid_from", "valid_to",
                  "amount", "left"]

    def get_left(self, row):
        return row.left()


class TcsCertificateViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """A buyer's certificate for collection at a lower rate (section 206C(9)), up to its amount (O619)."""

    queryset = TcsCertificate.objects.select_related("party", "section")
    serializer_class = TcsCertificateSerializer
    filter_fields = ["party", "section"]
    search_fields = ["party__name", "reference"]
    date_field = "valid_from"


class TcsDeclarationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """The buyer's declaration that its purchases are for its own manufacture; its form follows its date."""

    queryset = TcsDeclaration.objects.select_related("party", "section")
    serializer_class = TcsDeclarationSerializer
    filter_fields = ["party", "section"]
    search_fields = ["party__name", "reference"]
    date_field = "valid_from"
