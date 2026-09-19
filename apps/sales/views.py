from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import viewsets

from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.accounting.models import Account
from apps.core.audit import AuditableViewSetMixin

from .models import (
    Delivery,
    DeliveryLine,
    Invoice,
    InvoiceLine,
    InvoicePayment,
    SalesOrder,
    SalesOrderLine,
    ar_aging,
)
from .serializers import (
    DeliveryLineSerializer,
    DeliverySerializer,
    InvoiceLineSerializer,
    InvoicePaymentSerializer,
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

    @action(detail=False, methods=["get"])
    def aging(self, request):
        """AR aging: outstanding invoices bucketed by days overdue."""
        buckets = ar_aging(as_of=request.query_params.get("as_of"))
        return Response({
            key: {
                "count": bucket["count"],
                "total": bucket["total"],
                "invoices": [
                    {
                        "id": entry["invoice"].pk,
                        "number": entry["invoice"].number,
                        "customer": str(entry["invoice"].customer),
                        "due_date": entry["invoice"].due_date,
                        "days_overdue": entry["days_overdue"],
                        "amount_due": entry["amount_due"],
                    }
                    for entry in bucket["invoices"]
                ],
            }
            for key, bucket in buckets.items()
        })

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


class InvoicePaymentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = InvoicePayment.objects.select_related("invoice", "payment")
    serializer_class = InvoicePaymentSerializer

    def perform_create(self, serializer):
        try:
            super().perform_create(serializer)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)


class DeliveryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Delivery.objects.prefetch_related("lines")
    serializer_class = DeliverySerializer
    action_permission_map = {
        "post_delivery": "sales.post_delivery",
        "customer_return": "sales.post_delivery",
    }

    @action(detail=True, methods=["post"])
    def post_delivery(self, request, pk=None):
        delivery = self.get_object()
        try:
            delivery.post()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(delivery).data)

    @action(detail=True, methods=["post"])
    def customer_return(self, request, pk=None):
        delivery = self.get_object()
        try:
            returned = delivery.create_return()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(returned).data)


class DeliveryLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = DeliveryLine.objects.select_related("delivery", "order_line", "warehouse")
    serializer_class = DeliveryLineSerializer
