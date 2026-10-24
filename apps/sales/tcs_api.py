"""TCS collected on sale invoices: listed, listed for the quarter's return, and paid over by challan."""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.accounting.models import Account, TcsSection
from apps.core.api import record_or_404, required
from apps.core.models import to_date

from .tcs import TcsChallan, TcsCollection, tcs_return


class TcsCollectionSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    buyer_name = serializers.CharField(source="invoice.customer.name", read_only=True)
    # The section the row kept when it was issued (sales 0072), not its section as it reads today.
    section_code = serializers.CharField(read_only=True)
    # The challan that paid it over and still stands: a voided one paid nothing, and the screen read its
    # id as "Paid over" (review F11).
    challan = serializers.SerializerMethodField()

    def get_challan(self, row):
        return row.challan_id if row.is_paid_over() else None

    class Meta:
        model = TcsCollection
        fields = ["id", "invoice", "invoice_number", "buyer_name", "section", "section_code", "rate", "declaration",
                  "gives_back", "base", "rate_percent", "pan", "amount", "date", "journal_entry", "challan"]
        read_only_fields = fields


class TcsCollectionViewSet(viewsets.ReadOnlyModelViewSet):
    # Made by posting an invoice or its credit note, never typed in.
    queryset = TcsCollection.objects.select_related("invoice__customer", "section", "challan")
    serializer_class = TcsCollectionSerializer
    filter_fields = ["invoice", "section", "challan"]
    search_fields = ["invoice__number", "invoice__customer__name", "pan"]
    date_field = "date"
    action_permission_map = {"quarter": "sales.view_tcscollection"}

    @action(detail=False, methods=["get"])
    def quarter(self, request):
        """The return's rows (Form 143, was 27EQ) for ?from=&to= (the quarter's first and last days)."""
        start, end = to_date(request.query_params.get("from")), to_date(request.query_params.get("to"))
        if not start or not end:
            raise DRFValidationError({"from": ["Give the quarter's first and last days."]})
        return Response(tcs_return(start, end))


class TcsChallanSerializer(serializers.ModelSerializer):
    voided = serializers.BooleanField(source="voided_entry_id", read_only=True)

    class Meta:
        model = TcsChallan
        fields = ["id", "section", "section_code", "month", "date", "bank_account", "challan_number", "bsr_code",
                  "amount", "journal_entry", "voided_entry", "voided"]
        read_only_fields = fields


class TcsChallanViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TcsChallan.objects.select_related("section")
    serializer_class = TcsChallanSerializer
    filter_fields = ["section", "voided_entry__isnull"]
    search_fields = ["challan_number", "bsr_code"]
    date_field = "date"
    action_permission_map = {"pay": "sales.add_tcschallan", "void": "sales.post_invoice"}

    @action(detail=False, methods=["post"])
    def pay(self, request):
        """What was collected under one section paid over: {section, month, date, bank_account, challan_number, bsr_code}."""
        data = request.data
        section = record_or_404(TcsSection, data.get("section"), "section")
        bank = record_or_404(Account, data.get("bank_account"), "bank_account")
        required(data, "month", "date", "challan_number", "bsr_code")
        challan = TcsChallan.pay(section, data["month"], data["date"], bank,
                                 str(data["challan_number"]).strip(), str(data["bsr_code"]).strip())
        return Response(self.get_serializer(challan).data, status=201)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        challan = self.get_object()
        challan.void(on_date=request.data.get("date") or None)
        return Response(self.get_serializer(challan).data)
