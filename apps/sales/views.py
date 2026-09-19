from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import viewsets

from apps.accounting.models import Account
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
    action_permission_map = {"create_invoice": "sales.add_invoice"}

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        order = self.get_object()
        try:
            order.confirm()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def create_invoice(self, request, pk=None):
        order = self.get_object()
        account_id = request.data.get("receivable_account")
        if not account_id:
            raise DRFValidationError("receivable_account is required.")
        try:
            invoice = order.create_invoice(
                receivable_account=get_object_or_404(Account, pk=account_id),
                invoice_date=request.data.get("invoice_date"),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(InvoiceSerializer(invoice).data)


class SalesOrderLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalesOrderLine.objects.all()
    serializer_class = SalesOrderLineSerializer


class InvoiceViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Invoice.objects.prefetch_related("lines")
    serializer_class = InvoiceSerializer
    action_permission_map = {
        "post_invoice": "sales.post_invoice",
        "credit_note": "sales.post_invoice",
    }

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
