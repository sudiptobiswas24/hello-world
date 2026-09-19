from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import viewsets

from decimal import Decimal, InvalidOperation

from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.accounting.models import Account
from apps.core.audit import AuditableViewSetMixin

from django.http import HttpResponse

from .models import (
    CustomerProfile,
    Delivery,
    DunningLevel,
    DunningNotice,
    DeliveryLine,
    Invoice,
    InvoiceLine,
    InvoicePayment,
    PriceList,
    PriceListItem,
    Quotation,
    QuotationLine,
    SalesOrder,
    SalesOrderLine,
    ar_aging,
    outstanding_balance,
    revenue_report,
    run_dunning,
)
from .serializers import (
    CustomerProfileSerializer,
    DeliveryLineSerializer,
    DunningLevelSerializer,
    DunningNoticeSerializer,
    DeliverySerializer,
    InvoiceLineSerializer,
    InvoicePaymentSerializer,
    InvoiceSerializer,
    PriceListItemSerializer,
    PriceListSerializer,
    QuotationLineSerializer,
    QuotationSerializer,
    SalesOrderLineSerializer,
    SalesOrderSerializer,
)


class SalesOrderViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalesOrder.objects.prefetch_related("lines")
    serializer_class = SalesOrderSerializer
    action_permission_map = {"create_invoice": "sales.add_invoice"}

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """Pass {"ignore_credit_limit": true} to confirm over the limit."""
        order = self.get_object()
        try:
            order.confirm(
                ignore_credit_limit=bool(request.data.get("ignore_credit_limit", False))
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        try:
            order.cancel()
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

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        invoice = self.get_object()
        response = HttpResponse(invoice.render_pdf(), content_type="application/pdf")
        name = invoice.number or f"draft-{invoice.pk}"
        response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
        return response

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """Email the invoice as a PDF. {"to": "..."} overrides the recipient."""
        invoice = self.get_object()
        try:
            recipient = invoice.email_to_customer(
                to=request.data.get("to"),
                subject=request.data.get("subject"),
                body=request.data.get("body"),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response({"sent_to": recipient, "sent_at": invoice.sent_at})

    @action(detail=False, methods=["get"])
    def revenue(self, request):
        """Net revenue, grouped by ?group_by=customer|item|month."""
        try:
            rows = revenue_report(
                date_from=request.query_params.get("from"),
                date_to=request.query_params.get("to"),
                group_by=request.query_params.get("group_by", "customer"),
            )
        except ValueError as exc:
            raise DRFValidationError(str(exc))
        return Response(rows)

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
        """
        Credit the whole invoice, or pass
        {"quantities": {"<invoice_line_id>": "3"}} to credit part of it.
        """
        invoice = self.get_object()
        requested = request.data.get("quantities")
        quantities = None
        if requested:
            lines = {str(line.pk): line for line in invoice.lines.all()}
            try:
                quantities = {
                    lines[str(line_id)]: Decimal(str(quantity))
                    for line_id, quantity in requested.items()
                }
            except KeyError as exc:
                raise DRFValidationError(f"Line {exc} is not on this invoice.")
            except (InvalidOperation, TypeError):
                raise DRFValidationError("Quantities must be numbers.")
        try:
            credit_note = invoice.create_credit_note(
                memo=request.data.get("memo", ""), quantities=quantities
            )
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
        """
        Take goods back. Credits the invoices that billed them unless
        {"credit_invoices": false} is passed (a replacement, not a refund).
        """
        delivery = self.get_object()
        credit = request.data.get("credit_invoices", True)
        try:
            returned = delivery.create_return(credit_invoices=bool(credit))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        payload = self.get_serializer(returned).data
        payload["credit_notes"] = [
            {"id": note.pk, "number": note.number, "total": note.total()}
            for note in returned.credit_notes_created
        ]
        return Response(payload)


class DeliveryLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = DeliveryLine.objects.select_related("delivery", "order_line", "warehouse")
    serializer_class = DeliveryLineSerializer


class PriceListViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PriceList.objects.prefetch_related("entries")
    serializer_class = PriceListSerializer


class PriceListItemViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PriceListItem.objects.select_related("price_list", "item")
    serializer_class = PriceListItemSerializer


class CustomerProfileViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = CustomerProfile.objects.select_related("party", "price_list")
    serializer_class = CustomerProfileSerializer

    @action(detail=True, methods=["get"])
    def exposure(self, request, pk=None):
        """What this customer owes right now, against their limit."""
        profile = self.get_object()
        owed = outstanding_balance(profile.party)
        return Response({
            "customer": str(profile.party),
            "outstanding": owed,
            "credit_limit": profile.credit_limit,
            "available": (profile.credit_limit - owed) if profile.credit_limit is not None else None,
        })


class QuotationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Quotation.objects.prefetch_related("lines")
    serializer_class = QuotationSerializer
    action_permission_map = {"accept": "sales.add_salesorder"}

    @action(detail=True, methods=["post"])
    def mark_sent(self, request, pk=None):
        quotation = self.get_object()
        try:
            quotation.mark_sent()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(quotation).data)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        quotation = self.get_object()
        try:
            quotation.decline()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(quotation).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Convert to a confirmed sales order."""
        quotation = self.get_object()
        try:
            order = quotation.accept(
                order_date=request.data.get("order_date"),
                ignore_credit_limit=bool(request.data.get("ignore_credit_limit", False)),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(SalesOrderSerializer(order).data)


class QuotationLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = QuotationLine.objects.select_related("quotation", "item")
    serializer_class = QuotationLineSerializer


class DunningLevelViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = DunningLevel.objects.all()
    serializer_class = DunningLevelSerializer

    @action(detail=False, methods=["post"])
    def run(self, request):
        """
        Raise the reminders now due. {"send": false} records them without
        emailing, which is how you preview a run.
        """
        notices = run_dunning(
            as_of=request.data.get("as_of"),
            send=bool(request.data.get("send", True)),
        )
        return Response(DunningNoticeSerializer(notices, many=True).data)


class DunningNoticeViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = DunningNotice.objects.select_related("invoice", "level")
    serializer_class = DunningNoticeSerializer
