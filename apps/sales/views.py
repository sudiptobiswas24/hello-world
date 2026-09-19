from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine
from .serializers import (
    InvoiceLineSerializer,
    InvoiceSerializer,
    SalesOrderLineSerializer,
    SalesOrderSerializer,
)


class SalesOrderViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalesOrder.objects.prefetch_related("lines")
    serializer_class = SalesOrderSerializer


class SalesOrderLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalesOrderLine.objects.all()
    serializer_class = SalesOrderLineSerializer


class InvoiceViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Invoice.objects.prefetch_related("lines")
    serializer_class = InvoiceSerializer

    @action(detail=True, methods=["post"])
    def post_invoice(self, request, pk=None):
        invoice = self.get_object()
        try:
            invoice.post(memo=request.data.get("memo"))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(invoice).data)

    @action(detail=True, methods=["post"])
    def credit_note(self, request, pk=None):
        invoice = self.get_object()
        try:
            credit_note = invoice.create_credit_note(memo=request.data.get("memo", ""))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(credit_note).data)


class InvoiceLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = InvoiceLine.objects.all()
    serializer_class = InvoiceLineSerializer
