"""
TDS on what the company owes: deducted on a bill, reversed, paid over by
challan, and listed for the quarter's return.
"""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.accounting.models import Account, TdsSection
from apps.core.api import record_or_404, required
from apps.core.models import to_date

from .tds import TdsChallan, TdsDeduction, tds_return


class TdsDeductionSerializer(serializers.ModelSerializer):
    bill_number = serializers.CharField(source="bill.number", read_only=True)
    vendor_name = serializers.CharField(source="bill.vendor.name", read_only=True)
    section_code = serializers.CharField(source="section.code", read_only=True)
    challan_label = serializers.SerializerMethodField()
    reversed = serializers.BooleanField(source="reversed_entry_id", read_only=True)

    class Meta:
        model = TdsDeduction
        fields = ["id", "bill", "bill_number", "vendor_name", "section", "section_code", "base", "rate_percent",
                  "pan", "amount", "date", "covered_bills", "journal_entry", "reversed_entry", "reversed",
                  "challan", "challan_label"]
        read_only_fields = fields

    def get_challan_label(self, row):
        return str(row.challan) if row.is_paid_over() else ""


class TdsDeductionViewSet(viewsets.ReadOnlyModelViewSet):
    # Made by a bill (bills/<id>/deduct_tds/), never typed in.
    queryset = TdsDeduction.objects.select_related("bill__vendor", "section", "challan").prefetch_related(
        "covered_bills")
    serializer_class = TdsDeductionSerializer
    filter_fields = ["bill", "section", "challan", "reversed_entry__isnull"]
    search_fields = ["bill__number", "bill__vendor__name", "pan"]
    date_field = "date"
    action_permission_map = {"reverse": "purchasing.add_tdsdeduction",
                             "quarter": "purchasing.view_tdsdeduction"}

    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        deduction = self.get_object()
        deduction.reverse(on_date=request.data.get("date") or None)
        return Response(self.get_serializer(deduction).data)

    @action(detail=False, methods=["get"])
    def quarter(self, request):
        """The return's rows for ?from=&to= (the quarter's first and last days)."""
        start, end = to_date(request.query_params.get("from")), to_date(request.query_params.get("to"))
        if not start or not end:
            raise DRFValidationError({"from": ["Give the quarter's first and last days."]})
        return Response(tds_return(start, end))


class TdsChallanSerializer(serializers.ModelSerializer):
    section_code = serializers.CharField(source="section.code", read_only=True)
    voided = serializers.BooleanField(source="voided_entry_id", read_only=True)

    class Meta:
        model = TdsChallan
        fields = ["id", "section", "section_code", "month", "date", "bank_account", "challan_number", "bsr_code",
                  "amount", "journal_entry", "voided_entry", "voided"]
        read_only_fields = fields


class TdsChallanViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TdsChallan.objects.select_related("section")
    serializer_class = TdsChallanSerializer
    filter_fields = ["section", "voided_entry__isnull"]
    search_fields = ["challan_number", "bsr_code"]
    date_field = "date"
    action_permission_map = {"pay": "purchasing.add_tdschallan", "void": "purchasing.add_tdschallan"}

    @action(detail=False, methods=["post"])
    def pay(self, request):
        """A month's deductions under one section paid over: {section, month, date, bank_account, challan_number, bsr_code}."""
        data = request.data
        section = record_or_404(TdsSection, data.get("section"), "section")
        bank = record_or_404(Account, data.get("bank_account"), "bank_account")
        required(data, "month", "date", "challan_number", "bsr_code")
        challan = TdsChallan.pay(section, data["month"], data["date"], bank,
                                 str(data["challan_number"]).strip(), str(data["bsr_code"]).strip())
        return Response(self.get_serializer(challan).data, status=201)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        challan = self.get_object()
        challan.void(on_date=request.data.get("date") or None)
        return Response(self.get_serializer(challan).data)
