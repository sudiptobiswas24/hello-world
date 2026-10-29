from rest_framework import serializers

from .einvoice import EInvoice
from .ewaybill import EwayBill
from .gstr2b import Gstr2bStatement


class EInvoiceSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    warnings = serializers.SerializerMethodField()

    class Meta:
        model = EInvoice
        fields = ["id", "invoice", "invoice_number", "payload", "irn", "ack_number",
                  "ack_date", "signed_qr", "cancel_reason", "cancel_remarks", "cancel_payload",
                  "cancelled_at", "cancellation_note", "warnings", "created_at", "updated_at"]
        read_only_fields = fields

    def get_warnings(self, obj):
        return obj.warnings()


class EwayBillSerializer(serializers.ModelSerializer):
    class Meta:
        model = EwayBill
        fields = ["id", "invoice", "challan", "delivery", "mode", "distance_km", "transporter_id",
                  "transporter_name", "vehicle_number", "vehicle_type",
                  "transport_doc_number", "transport_doc_date", "payload", "required",
                  "required_because", "number", "generated_at", "valid_until",
                  "cancelled_at", "cancel_reason", "cancel_remarks", "created_at",
                  "updated_at"]
        read_only_fields = fields


class Gstr2bStatementSerializer(serializers.ModelSerializer):
    line_count = serializers.IntegerField(read_only=True)
    kept_by = serializers.SerializerMethodField()

    class Meta:
        model = Gstr2bStatement
        fields = ["id", "period", "gstin", "generated_on", "source", "note", "line_count", "kept_by", "created_at"]
        read_only_fields = ["period", "gstin", "generated_on", "source", "note"]

    def get_kept_by(self, row):
        user = row.created_by
        return (user.get_full_name() or user.username) if user else ""


from .reverse_charge import GstCashPayment, PaymentVoucher, SelfInvoice  # noqa: E402


class SelfInvoiceSerializer(serializers.ModelSerializer):
    bill_number = serializers.CharField(source="bill.number", read_only=True)

    class Meta:
        model = SelfInvoice
        fields = ["id", "number", "bill", "bill_number", "date", "received_on", "supplier_name", "place_of_supply",
                  "taxable", "igst", "cgst", "sgst", "cess", "cancelled_on", "cancellation_reason"]
        read_only_fields = fields


class PaymentVoucherSerializer(serializers.ModelSerializer):
    bill_number = serializers.CharField(source="bill.number", read_only=True)
    payment_number = serializers.CharField(source="payment.number", read_only=True)

    class Meta:
        model = PaymentVoucher
        fields = ["id", "number", "payment", "payment_number", "bill", "bill_number", "date", "amount",
                  "igst", "cgst", "sgst", "cess", "cancelled_on"]
        read_only_fields = fields


class GstCashPaymentSerializer(serializers.ModelSerializer):
    voided = serializers.BooleanField(source="voided_entry_id", read_only=True)

    class Meta:
        model = GstCashPayment
        fields = ["id", "month", "date", "cpin", "bank_account", "cash_ledger_account", "igst", "cgst", "sgst",
                  "cess", "journal_entry", "voided_entry", "voided"]
        read_only_fields = fields
