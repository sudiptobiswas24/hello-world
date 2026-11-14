"""The bill of entry (L21), rate contracts and rate-difference notes (L16), over the API."""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import record_or_404
from apps.core.audit import AuditableViewSetMixin

from .imports import BillOfEntry, BillOfEntryLine, CustomsValuationDefault
from .models import Bill, GoodsReceipt
from .rate_contracts import PurchaseRateContract, RateContractLine, RateDifferenceLine, RateDifferenceNote


class BillOfEntryLineSerializer(serializers.ModelSerializer):
    receipt_number = serializers.CharField(source="receipt_line.receipt.number", read_only=True)
    item_name = serializers.CharField(source="receipt_line.order_line.item.name", read_only=True)
    bill_number = serializers.CharField(source="bill_line.bill.number", read_only=True)

    class Meta:
        model = BillOfEntryLine
        fields = ["id", "entry", "receipt_line", "receipt_number", "bill_line", "bill_number", "item_name",
                  "quantity", "fob", "assessable_value", "bcd_rate", "bcd", "sws_rate", "sws", "igst_rate", "igst"]
        read_only_fields = ["entry", "receipt_line", "bill_line", "quantity", "fob", "assessable_value", "bcd",
                            "sws", "igst"]


class BillOfEntrySerializer(serializers.ModelSerializer):
    customs_party_name = serializers.CharField(source="customs_party.name", read_only=True)
    currency_code = serializers.CharField(source="currency.code", read_only=True)
    duty_bill_number = serializers.CharField(source="duty_bill.number", read_only=True, default="")
    void_note_number = serializers.CharField(source="void_note.number", read_only=True, default="")
    payable_account_name = serializers.CharField(source="payable_account.__str__", read_only=True)
    duty_account_name = serializers.CharField(source="duty_account.__str__", read_only=True)
    igst_account_name = serializers.CharField(source="igst_account.__str__", read_only=True)
    supersedes_number = serializers.CharField(source="supersedes.number", read_only=True, default="")
    superseded_by = serializers.SerializerMethodField()
    fob_total = serializers.SerializerMethodField()
    assessable_total = serializers.SerializerMethodField()
    bcd_total = serializers.SerializerMethodField()
    sws_total = serializers.SerializerMethodField()
    duty_total = serializers.SerializerMethodField()
    igst_total = serializers.SerializerMethodField()

    class Meta:
        model = BillOfEntry
        fields = ["id", "number", "be_number", "be_date", "port_code", "currency", "currency_code", "customs_rate",
                  "freight", "insurance", "freight_taken", "insurance_taken", "valuation_rule", "bcd_rate",
                  "sws_rate", "igst_rate", "customs_party", "customs_party_name", "payable_account", "duty_account",
                  "igst_account", "payable_account_name", "duty_account_name", "igst_account_name",
                  "supersedes_number", "basis", "status", "duty_bill", "duty_bill_number", "void_note",
                  "void_note_number", "voided_on", "void_reason", "supersedes", "superseded_by", "fob_total",
                  "assessable_total", "bcd_total", "sws_total", "duty_total", "igst_total"]
        read_only_fields = ["number", "freight_taken", "insurance_taken", "valuation_rule", "status", "duty_bill",
                            "void_note", "voided_on", "void_reason"]

    def get_superseded_by(self, entry):
        follower = BillOfEntry.objects.filter(supersedes=entry).first()
        return follower.pk if follower else None

    def _total(self, entry, key):
        if not hasattr(entry, "_totals"):
            entry._totals = entry.totals()
        return entry._totals[key]

    def get_fob_total(self, entry):
        return self._total(entry, "fob")

    def get_assessable_total(self, entry):
        return self._total(entry, "assessable_value")

    def get_bcd_total(self, entry):
        return self._total(entry, "bcd")

    def get_sws_total(self, entry):
        return self._total(entry, "sws")

    def get_duty_total(self, entry):
        return self._total(entry, "bcd") + self._total(entry, "sws")

    def get_igst_total(self, entry):
        return self._total(entry, "igst")


class BillOfEntryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Goods imported, cleared through customs: assessed, posted, voided or re-assessed (L21)."""

    serializer_class = BillOfEntrySerializer
    queryset = BillOfEntry.objects.select_related("customs_party", "currency", "duty_bill", "void_note")
    filter_fields = ["status", "port_code", "customs_party"]
    search_fields = ["number", "be_number", "port_code"]
    date_field = "be_date"
    ordering_fields = ["be_date", "number", "be_number"]
    action_permission_map = {
        "clear_receipt": "purchasing.change_billofentry",
        "post": "purchasing.post_billofentry",
        "void": "purchasing.post_billofentry",
        "document": "purchasing.view_billofentry",
    }

    @action(detail=True, methods=["post"])
    def clear_receipt(self, request, pk=None):
        """{receipt, bill}: the receipt's goods, as the overseas supplier's bill invoiced them."""
        entry = self.get_object()
        receipt = record_or_404(GoodsReceipt, request.data.get("receipt"), "receipt")
        bill = record_or_404(Bill, request.data.get("bill"), "bill")
        entry.clear_receipt(receipt, bill)
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        entry = self.get_object()
        entry.post(by=request.user)
        entry.refresh_from_db()
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        entry = self.get_object()
        entry.void(on_date=request.data.get("on_date") or None, reason=request.data.get("reason") or "")
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["get"])
    def document(self, request, pk=None):
        """The bill of entry as the books hold it, printed (PDF)."""
        from .imports_pdf import bill_of_entry_pdf

        return bill_of_entry_pdf(self.get_object())


class BillOfEntryLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    serializer_class = BillOfEntryLineSerializer
    queryset = BillOfEntryLine.objects.select_related("receipt_line__receipt", "receipt_line__order_line__item",
                                                      "bill_line__bill")
    http_method_names = ["get", "patch", "delete", "head", "options"]
    filter_fields = ["entry"]
    search_fields = ["receipt_line__receipt__number", "receipt_line__order_line__item__name"]
    ordering_fields = ["id"]


class CustomsValuationDefaultSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomsValuationDefault
        fields = ["id", "valid_from", "freight_percent", "insurance_percent", "citation", "confirmed"]


class CustomsValuationDefaultViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    serializer_class = CustomsValuationDefaultSerializer
    queryset = CustomsValuationDefault.objects.all()
    http_method_names = ["get", "post", "patch", "head", "options"]
    search_fields = ["citation"]
    ordering_fields = ["valid_from"]


class RateContractLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    ordered_kg = serializers.SerializerMethodField()

    class Meta:
        model = RateContractLine
        fields = ["id", "contract", "item", "item_name", "quantity_kg", "ordered_kg"]

    def get_ordered_kg(self, line):
        return line.ordered_kg()


class PurchaseRateContractSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    index_code = serializers.CharField(source="index.code", read_only=True)
    index_today = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseRateContract
        fields = ["id", "number", "vendor", "vendor_name", "index", "index_code", "discount_per_kg", "valid_from",
                  "valid_to", "note", "index_today"]
        read_only_fields = ["number"]

    def get_index_today(self, contract):
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        try:
            return contract.index.value_on(timezone.localdate())
        except ValidationError:
            return None


class PurchaseRateContractViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """A polymer supplier's rate contract, priced on a maker's circular less a discount a kg (L16)."""

    serializer_class = PurchaseRateContractSerializer
    queryset = PurchaseRateContract.objects.select_related("vendor", "index")
    filter_fields = ["vendor", "index"]
    search_fields = ["number", "vendor__name", "index__code"]
    date_field = "valid_from"
    ordering_fields = ["valid_from", "number"]


class RateContractLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    serializer_class = RateContractLineSerializer
    queryset = RateContractLine.objects.select_related("item", "contract")
    filter_fields = ["contract"]
    search_fields = ["item__name", "item__sku"]
    ordering_fields = ["id"]


class RateDifferenceLineSerializer(serializers.ModelSerializer):
    receipt_number = serializers.CharField(source="receipt_line.receipt.number", read_only=True)
    receipt_date = serializers.DateField(source="receipt_line.receipt.receipt_date", read_only=True)
    item_name = serializers.CharField(source="order_line.item.name", read_only=True)

    class Meta:
        model = RateDifferenceLine
        fields = ["id", "note", "receipt_line", "receipt_number", "receipt_date", "order_line", "item_name",
                  "quantity_kg", "index_was", "index_now", "per_kg", "amount"]
        read_only_fields = fields


class RateDifferenceNoteSerializer(serializers.ModelSerializer):
    contract_number = serializers.CharField(source="contract.number", read_only=True)
    vendor_name = serializers.CharField(source="contract.vendor.name", read_only=True)
    bill_number = serializers.CharField(source="bill.__str__", read_only=True, default="")
    debit_note_number = serializers.CharField(source="debit_note.__str__", read_only=True, default="")
    total = serializers.SerializerMethodField()

    class Meta:
        model = RateDifferenceNote
        fields = ["id", "number", "contract", "contract_number", "vendor_name", "start", "end", "note_date",
                  "supplier_reference", "supplier_note_date", "account", "status", "bill", "bill_number",
                  "debit_note", "debit_note_number", "cancelled_at", "total"]
        read_only_fields = ["number", "account", "status", "bill", "debit_note", "cancelled_at"]

    def get_total(self, note):
        return note.total()


class RateDifferenceNoteViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """The supplier's note for a circular revised back-dated: drafted as a bill or a debit note (L16)."""

    serializer_class = RateDifferenceNoteSerializer
    queryset = RateDifferenceNote.objects.select_related("contract__vendor", "bill", "debit_note")
    http_method_names = ["get", "post", "head", "options"]
    filter_fields = ["status", "contract"]
    search_fields = ["number", "supplier_reference", "contract__vendor__name"]
    date_field = "note_date"
    ordering_fields = ["note_date", "number"]
    action_permission_map = {"cancel": "purchasing.change_ratedifferencenote"}

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        note = self.get_object()
        note.cancel()
        return Response(self.get_serializer(note).data)


class RateDifferenceLineViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = RateDifferenceLineSerializer
    queryset = RateDifferenceLine.objects.select_related("receipt_line__receipt", "order_line__item")
    filter_fields = ["note"]
    search_fields = ["receipt_line__receipt__number"]
    ordering_fields = ["id"]
