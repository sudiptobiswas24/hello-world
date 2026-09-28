from rest_framework import serializers

from .einvoice import EInvoice
from .ewaybill import EwayBill


class EInvoiceSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    warnings = serializers.SerializerMethodField()

    class Meta:
        model = EInvoice
        fields = ["id", "invoice", "invoice_number", "payload", "irn", "ack_number",
                  "ack_date", "signed_qr", "warnings", "created_at", "updated_at"]
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
