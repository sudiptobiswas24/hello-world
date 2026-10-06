"""
Bad debt through the API: the controller writes off what will not be
paid, and reverses a write-off when the money comes after all.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from .models import InvoiceWriteOff
from .scoping import CustomerScopedMixin


class InvoiceWriteOffSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    customer_name = serializers.CharField(source="invoice.customer.name", read_only=True)
    is_recovered = serializers.BooleanField(read_only=True)

    class Meta:
        model = InvoiceWriteOff
        fields = ["id", "invoice", "invoice_number", "customer_name", "amount", "date", "reason",
                  "journal_entry", "recovered_entry", "is_recovered"]
        read_only_fields = fields


class InvoiceWriteOffViewSet(CustomerScopedMixin, viewsets.ReadOnlyModelViewSet):
    # Raised by Invoice.write_off() and never typed in: it is a ledger event.
    customer_path = "invoice__customer"
    queryset = InvoiceWriteOff.objects.select_related("invoice__customer")
    serializer_class = InvoiceWriteOffSerializer
    filter_fields = ["invoice"]
    search_fields = ["invoice__number", "invoice__customer__name", "reason"]
    date_field = "date"
    action_permission_map = {"recover": "sales.write_off_invoice"}

    @action(detail=True, methods=["post"])
    def recover(self, request, pk=None):
        write_off = self.get_object()
        try:
            write_off.invoice.recover_write_off(write_off, on_date=request.data.get("date") or None)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        write_off.refresh_from_db()
        return Response(self.get_serializer(write_off).data)
