"""Tax customers deducted: recorded from the invoice, confirmed against Form 26AS, or reversed."""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.accounting.models import TdsSection
from apps.core.api import money_amount, record_or_404

from .models import Invoice
from .scoping import UNLIMITED, CustomerScopedMixin, carried_by, rep_limit
from .tds import CustomerTds


class CustomerTdsSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    customer_name = serializers.CharField(source="invoice.customer.name", read_only=True)
    section_code = serializers.CharField(source="section.code", read_only=True)
    reversed = serializers.SerializerMethodField()

    class Meta:
        model = CustomerTds
        fields = ["id", "invoice", "invoice_number", "customer_name", "section", "section_code", "amount", "date",
                  "certificate", "confirmed_on", "journal_entry", "reversed_entry", "reversed"]
        read_only_fields = fields

    def get_reversed(self, row):
        return bool(row.reversed_entry_id)


class CustomerTdsViewSet(CustomerScopedMixin, viewsets.ReadOnlyModelViewSet):
    # Recorded from the invoice (invoices/<id>/record_tds/), never typed in.
    customer_path = "invoice__customer"
    queryset = CustomerTds.objects.select_related("invoice__customer", "section")
    serializer_class = CustomerTdsSerializer
    filter_fields = ["invoice", "section", "confirmed_on__isnull", "reversed_entry__isnull"]
    search_fields = ["invoice__number", "invoice__customer__name", "certificate"]
    date_field = "date"
    action_permission_map = {"record": "sales.add_customertds",
                             **{name: "sales.change_customertds" for name in ("reverse", "confirm", "unconfirm")}}

    @action(detail=False, methods=["post"])
    def record(self, request):
        """The customer paid an invoice short by its TDS: {invoice, section, amount, date, certificate}."""
        data = request.data
        invoice = record_or_404(Invoice, data.get("invoice"), "invoice")
        rep = rep_limit(request.user)
        if rep is not UNLIMITED and not Invoice.objects.filter(carried_by(rep, "customer"), pk=invoice.pk).exists():
            raise DRFValidationError({"invoice": ["Not one of the invoices you can see."]})
        section = record_or_404(TdsSection, data.get("section"), "section")
        amount = money_amount(data, "amount")
        if amount is None:
            raise DRFValidationError({"amount": ["Say how much the customer deducted."]})
        try:
            row = invoice.record_tds(section, amount, on_date=data.get("date") or None,
                                     certificate=data.get("certificate") or "")
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(row).data, status=201)

    def _do(self, call):
        row = self.get_object()
        try:
            call(row)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        row.refresh_from_db()
        return Response(self.get_serializer(row).data)

    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        return self._do(lambda row: row.reverse(on_date=request.data.get("date") or None))

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """Found in Form 26AS ({date, certificate})."""
        return self._do(lambda row: row.confirm(on_date=request.data.get("date") or None,
                                                certificate=request.data.get("certificate") or None))

    @action(detail=True, methods=["post"])
    def unconfirm(self, request, pk=None):
        return self._do(lambda row: row.unconfirm())
