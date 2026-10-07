from types import SimpleNamespace

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import viewsets

from decimal import Decimal, InvalidOperation

from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import ActionPermission, RequiredPermission
from rest_framework.response import Response

from apps.core.api import flag, money_amount, quantities_by_line, record_or_404
from apps.accounting.models import PartyTaxProfile
from apps.accounting.serializers import PartyTaxProfileSerializer
from apps.core.models import Party, PartyRole, to_date
from apps.core.views import NewPartyViewSet

from apps.accounting.defaults import chosen_or_default
from apps.core.audit import AuditableViewSetMixin
from apps.inventory.models import Warehouse

from django.http import HttpResponse
from django.utils import timezone

from django.db.models import Count, Prefetch

from .teams import SalesTarget, SalesTeam, targets_report
from .documents import render_pick_list_pdf
from .models import (
    deliveries_to_pick,
    pick_list_for,
    INVOICE_FIGURES,
    SuppliedItem,
    ThirdPartyRelease,
    ThirdPartyReleaseLine,
    bad_debt_report,
    send_statements,
    CommissionPlan,
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
    RecurringInvoice,
    RecurringInvoiceLine,
    SalesOrder,
    SalesOrderLine,
    SalesRep,
    ar_aging,
    commission_report,
    generate_due_invoices,
    outstanding_balance,
    orders_to_ship,
    revenue_report,
    run_dunning,
    still_owed,
)
from .scoping import CustomerScopedMixin
from .serializers import (
    SalesTargetSerializer,
    SalesTeamSerializer,
    SuppliedItemSerializer,
    ThirdPartyReleaseSerializer,
    CommissionPlanSerializer,
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
    RecurringInvoiceLineSerializer,
    RecurringInvoiceSerializer,
    SalesOrderLineSerializer,
    SalesOrderSerializer,
    SalesRepSerializer,
)


def _order_lines():
    """Order lines with what their shipped and invoiced quantities read."""
    return SalesOrderLine.objects.select_related("item", "uom", "charge").prefetch_related(
        "taxes",
        Prefetch("delivery_lines", queryset=DeliveryLine.objects.select_related("delivery")),
        Prefetch("invoice_lines", queryset=InvoiceLine.objects.select_related("invoice")),
    )


class SalesOrderViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    extra_params = ('to_ship',)
    search_fields = ["number", "reference", "customer__code", "customer__name"]
    filter_fields = ["customer", "status", "sales_rep", "is_job_work"]
    date_field = "order_date"
    ordering_fields = ["order_date", "number"]

    queryset = SalesOrder.objects.select_related(
        "customer__tax_profile", "currency", "payment_terms"
    ).prefetch_related(Prefetch("lines", queryset=_order_lines()), "supplied_items")
    serializer_class = SalesOrderSerializer
    action_permission_map = {
        "ship": "sales.add_delivery",
        "create_invoice": "sales.add_invoice",
        "down_payment": "sales.add_invoice",
        "approve": "sales.approve_order",
        "confirm": "sales.change_salesorder",
        "cancel": "sales.change_salesorder",
        "send": "sales.change_salesorder",
    }

    @staticmethod
    def _proforma(data):
        kind = str(data.get("kind") or "acknowledgement")
        if kind not in ("acknowledgement", "proforma"):
            raise DRFValidationError({"kind": ["The order prints as an acknowledgement or a proforma."]})
        return kind == "proforma"

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """?kind=acknowledgement (the default) or proforma."""
        order = self.get_object()
        proforma = self._proforma(request.query_params)
        response = HttpResponse(order.render_pdf(proforma=proforma), content_type="application/pdf")
        name = f"{'proforma' if proforma else 'acknowledgement'}-{order.number or f'draft-{order.pk}'}"
        response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
        return response

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """Email the acknowledgement or the proforma ({"kind"}); {"to", "subject", "body"} override the wording."""
        order = self.get_object()
        try:
            recipient = order.email_to_customer(to=request.data.get("to") or None,
                                                subject=request.data.get("subject") or None,
                                                body=request.data.get("body") or None, user=request.user,
                                                proforma=self._proforma(request.data))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response({"sent_to": recipient})

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        # ?to_ship=true: confirmed, with goods still owed.
        if flag(self.request.query_params, "to_ship", False):
            queryset = queryset.filter(pk__in=orders_to_ship(queryset))
        return queryset

    @action(detail=True, methods=["post"])
    def ship(self, request, pk=None):
        """{delivery_date?, warehouse?}: a draft delivery of what is still owed."""
        order = self.get_object()
        warehouse = request.data.get("warehouse")
        delivery = order.create_delivery(
            delivery_date=request.data.get("delivery_date"),
            warehouse=record_or_404(Warehouse, warehouse, "warehouse", optional=True),
        )
        return Response(DeliverySerializer(delivery).data, status=201)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        order = self.get_object()
        try:
            order.confirm()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["get"])
    def approval(self, request, pk=None):
        """What, if anything, is holding this order up."""
        order = self.get_object()
        return Response({
            "status": order.approval_status(),
            "reasons": order.approval_reasons(),
            "margin_percent": order.margin_percent(),
            "approved_by": str(order.approved_by) if order.approved_by_id else None,
            "approved_at": order.approved_at,
        })

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        order = self.get_object()
        try:
            order.approve(by=request.user, note=request.data.get("note", ""))
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
        account = chosen_or_default(request.data, "receivable_account", "receivable")
        try:
            invoice = order.create_invoice(
                receivable_account=account,
                invoice_date=request.data.get("invoice_date"),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(InvoiceSerializer(invoice).data)

    @action(detail=True, methods=["post"])
    def down_payment(self, request, pk=None):
        """
        {receivable_account?, amount | percent, invoice_date?, description?}:
        a draft down-payment invoice. The model had it; nothing outside
        code could reach it, so a screen could not take an advance.
        """
        order = self.get_object()
        account = chosen_or_default(request.data, "receivable_account", "receivable")
        figures = {}
        for name in ("amount", "percent"):
            value = request.data.get(name)
            if value not in (None, ""):
                try:
                    figures[name] = Decimal(str(value))
                except InvalidOperation:
                    raise DRFValidationError(f"{name} must be a number.")
        try:
            invoice = order.create_down_payment_invoice(
                account,
                invoice_date=request.data.get("invoice_date"),
                description=request.data.get("description", ""),
                **figures,
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(InvoiceSerializer(invoice).data, status=201)


class SalesOrderLineViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "order__customer"
    queryset = _order_lines().select_related("order__customer__tax_profile")
    serializer_class = SalesOrderLineSerializer
    filter_fields = ["order", "order__status", "order__customer", "item", "order__is_job_work"]
    search_fields = ["order__number", "order__customer__name", "item__sku", "item__name"]
    action_permission_map = {"close_short": "sales.change_salesorder",
                             "reopen": "sales.change_salesorder"}

    @action(detail=True, methods=["post"], url_path="close-short")
    def close_short(self, request, pk=None):
        """{reason}: the customer wants no more of this line."""
        line = self.get_object()
        try:
            line.close_short(request.data.get("reason", ""))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(line).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        line = self.get_object()
        try:
            line.reopen()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(line).data)

    @action(detail=True, methods=["get"])
    def schedule(self, request, pk=None):
        """What is still owed, and by when: the call-offs, then the uncalled rest."""
        from .call_offs import called_off, open_schedule

        line = self.get_object()
        return Response({
            "quantity": str(line.quantity), "called_off": str(called_off(line).quantize(Decimal("0.0001"))),
            "open": [{"due_on": day, "quantity": str(owed)} for day, owed in open_schedule(line)],
        })


class SuppliedItemViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "order__customer"
    """What the customer sends for a job-work order; settled once it is confirmed."""

    queryset = SuppliedItem.objects.select_related("order", "item")
    serializer_class = SuppliedItemSerializer
    filter_fields = ["order", "item"]
    search_fields = ["order__number", "item__sku", "item__name"]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def perform_create(self, serializer):
        try:
            serializer.save()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)


class InvoiceViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    extra_params = ("open", "without_irn")
    search_fields = ["number", "reference", "customer__code", "customer__name", "sales_order__number"]
    filter_fields = ["customer", "posted", "credits", "credits__isnull", "is_down_payment",
                     "sales_order", "receivable_account", "currency"]
    date_field = "invoice_date"
    ordering_fields = ["invoice_date", "due_date", "number"]

    queryset = Invoice.objects.select_related(
        "customer__tax_profile", "currency", "payment_terms", "credits"
    ).prefetch_related(*INVOICE_FIGURES, "lines__item", "lines__charge")
    serializer_class = InvoiceSerializer

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        # ?open=true: posted invoices that still owe money, as amount_due()
        # reckons it.
        if flag(self.request.query_params, "open", False):
            queryset = still_owed(queryset)
        # ?without_irn=true: those e-invoicing covers that the portal has
        # not registered (the morning check's list).
        if flag(self.request.query_params, "without_irn", False):
            from apps.web.checks import invoices_without_irn

            queryset = invoices_without_irn(queryset)
        return queryset
    action_permission_map = {
        "post_invoice": "sales.post_invoice",
        "credit_note": "sales.post_invoice",
        "credit_old_supply": "sales.post_invoice",
        "write_off": "sales.write_off_invoice",
        "claim": "sales.post_invoice",
        "claims": "sales.view_invoice",
    }

    @action(detail=False, methods=["post"])
    def claim(self, request):
        """Money given back on a customer's claim: {invoice, net, reason, memo, date}; the credit note made."""
        given = str(request.data.get("invoice") or "")
        invoice = self.get_queryset().filter(pk=int(given)).first() if given.isdigit() else None
        if invoice is None:
            raise DRFValidationError({"invoice": ["Not one of the invoices you can see."]})
        net = money_amount(request.data, "net")
        if net is None:
            raise DRFValidationError({"net": ["Say how much, before tax, is given back."]})
        note = invoice.credit_claim(net, request.data.get("reason") or "", memo=request.data.get("memo") or "",
                                    on_date=request.data.get("date") or None)
        return Response(self.get_serializer(note).data, status=201)

    @action(detail=False, methods=["get"])
    def claims(self, request):
        """Claims credited ?from= to ?to=: what quality cost."""
        from .models import claims

        start, end = to_date(request.query_params.get("from")), to_date(request.query_params.get("to"))
        if not start or not end:
            raise DRFValidationError({"from": ["Give the first and last days."]})
        return Response(claims(start, end))

    @action(detail=True, methods=["post"])
    def write_off(self, request, pk=None):
        """What is left, or {"amount": "..."} of it, to bad debt; with a reason."""
        invoice = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            # The model takes a blank reason; an auditor asking why a
            # receivable vanished does not.
            raise DRFValidationError({"reason": ["Say why it will not be paid."]})
        try:
            invoice.write_off(amount=money_amount(request.data, "amount"),
                              on_date=request.data.get("date") or None, reason=reason)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        invoice.refresh_from_db()
        return Response(self.get_serializer(invoice).data)

    @action(detail=True, methods=["post"])
    def credit_old_supply(self, request, pk=None):
        """
        {lines: [{item?, description, quantity, unit_price, taxes, revenue_account?}],
        memo?, on_date?, old_value?}: a credit note with GST on an invoice the old
        system issued. A line's account is the company's revenue account unless named.
        """
        from apps.accounting.defaults import default_account
        from apps.accounting.old_supply import from_request
        from apps.inventory.models import Item

        invoice = self.get_object()
        try:
            usual = default_account("revenue")
        except DjangoValidationError:
            usual = None  # each line then names its own, or is refused
        lines, memo, on_date, old_value = from_request(request.data, "revenue_account", Item, usual)
        try:
            note = invoice.credit_old_supply(lines, memo=memo, on_date=on_date, old_value=old_value)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(note).data, status=201)

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
        """Net revenue, grouped by ?group_by=customer|item|month|rep|industry."""
        try:
            rows = revenue_report(
                date_from=request.query_params.get("from"),
                date_to=request.query_params.get("to"),
                group_by=request.query_params.get("group_by", "customer"),
                invoices=self.get_queryset(),
            )
        except ValueError as exc:
            raise DRFValidationError(str(exc))
        return Response(rows)

    @action(detail=False, methods=["get"])
    def aging(self, request):
        """AR aging: outstanding invoices bucketed by days overdue."""
        buckets = ar_aging(as_of=request.query_params.get("as_of"), invoices=self.get_queryset())
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
        quantities = quantities_by_line(request.data.get("quantities"), invoice.lines.all(), "invoice")
        amount = money_amount(request.data, "amount")  # a down payment's, part of what is left
        try:
            credit_note = invoice.create_credit_note(
                memo=request.data.get("memo", ""), quantities=quantities, amount=amount
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(credit_note).data)


class InvoiceLineViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "invoice__customer"
    queryset = InvoiceLine.objects.select_related(
        "invoice__customer__tax_profile", "item", "credits_line"
    ).prefetch_related("taxes", "recorded_taxes__tax")
    serializer_class = InvoiceLineSerializer


class InvoicePaymentViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "invoice__customer"
    filter_fields = ["invoice", "payment"]

    queryset = InvoicePayment.objects.select_related("invoice", "payment")
    serializer_class = InvoicePaymentSerializer

    def perform_create(self, serializer):
        try:
            super().perform_create(serializer)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)


def _pick_rows(rows):
    """A pick list as the office reads it: codes and labels, the quantity exact."""
    return [{
        "id": index, "warehouse": row["warehouse"].code,
        "bin": row["bin"].code if row["bin"] else "", "bin_id": row["bin"].pk if row["bin"] else None,
        "item": f"{row['item'].sku} · {row['item'].name}", "item_id": row["item"].pk,
        "lot": row["lot"].code if row["lot"] else "", "lot_id": row["lot"].pk if row["lot"] else None,
        "quantity": row["quantity"], "for": row["for"], "problem": row["problem"],
    } for index, row in enumerate(rows, start=1)]


def _where(lines):
    """The warehouse a delivery picks at, or a name for several."""
    warehouses = {line.warehouse for line in lines}
    return warehouses.pop() if len(warehouses) == 1 else SimpleNamespace(name="Several warehouses")


def _paper(pdf, filename):
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


class DeliveryViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "sales_order__customer"
    search_fields = ["number", "reference", "sales_order__number", "sales_order__customer__code", "sales_order__customer__name",
                     "lr_number", "vehicle_number"]
    filter_fields = ["sales_order", "sales_order__customer", "posted", "reverses", "reverses__isnull", "transporter",
                     "freight_charge__isnull", "received_on__isnull"]
    date_field = "delivery_date"
    ordering_fields = ["delivery_date", "number"]

    queryset = Delivery.objects.select_related("sales_order__customer").prefetch_related(
        Prefetch("lines", queryset=DeliveryLine.objects.select_related("order_line__item")))
    serializer_class = DeliverySerializer
    action_permission_map = {
        "post_delivery": "sales.post_delivery",
        "customer_return": "sales.post_delivery",
        "transport": "sales.change_delivery",
        "received": "sales.change_delivery",
        "send": "sales.change_delivery",
        "unacknowledged": "sales.view_delivery",
        "pick_list": "sales.view_delivery",
        "pick_list_pdf": "sales.view_delivery",
        "day_pick_list": "sales.view_delivery",
        "day_pick_list_pdf": "sales.view_delivery",
    }

    # Named "picking", not "pick-list": a route name ending in -list reads as a collection.
    @action(detail=True, methods=["get"], url_path="pick-list", url_name="picking")
    def pick_list(self, request, pk=None):
        """The route through the shelves for this draft shipment, in walking order."""
        delivery = self.get_object()
        return Response({"delivery": delivery.pk, "number": delivery.number, "rows": _pick_rows(delivery.pick_list())})

    @action(detail=True, methods=["get"], url_path="pick-list/pdf", url_name="picking-pdf")
    def pick_list_pdf(self, request, pk=None):
        delivery = self.get_object()
        paper = render_pick_list_pdf(
            delivery.pick_list(), heading="Pick List", document=delivery, where=_where(delivery.lines.all()),
            meta=[["Delivery", delivery.number or "(draft)"], ["Date", f"{delivery.delivery_date:%d %b %Y}"],
                  ["Customer", delivery.sales_order.customer.name], ["Order", delivery.sales_order.number or ""]])
        return _paper(paper, f"pick-{delivery.number or f'draft-{delivery.pk}'}.pdf")

    def _days_picking(self, request):
        day = to_date(request.query_params.get("date")) or timezone.localdate()
        warehouse = record_or_404(Warehouse, request.query_params.get("warehouse"), "warehouse", optional=True)
        deliveries = list(deliveries_to_pick(day, warehouse).filter(
            pk__in=self.filter_queryset(self.get_queryset()).values("pk")))
        return day, warehouse, deliveries, pick_list_for(deliveries, warehouse=warehouse)

    @action(detail=False, methods=["get"], url_path="pick-list", url_name="day-picking")
    def day_pick_list(self, request):
        """One route for the day's draft shipments (?date=, today by default; ?warehouse= to keep to one)."""
        day, warehouse, deliveries, rows = self._days_picking(request)
        return Response({"date": day, "warehouse": warehouse.pk if warehouse else None,
                         "deliveries": [delivery.number or f"Draft {delivery.pk}" for delivery in deliveries],
                         "rows": _pick_rows(rows)})

    @action(detail=False, methods=["get"], url_path="pick-list/pdf", url_name="day-picking-pdf")
    def day_pick_list_pdf(self, request):
        day, warehouse, deliveries, rows = self._days_picking(request)
        paper = render_pick_list_pdf(
            rows, heading="Pick List", document=SimpleNamespace(number="", lines=[]),
            where=warehouse or SimpleNamespace(name="Every warehouse"),
            meta=[["Date", f"{day:%d %b %Y}"], ["Deliveries", str(len(deliveries))]])
        return _paper(paper, f"pick-{day:%Y-%m-%d}.pdf")

    @action(detail=True, methods=["post"])
    def received(self, request, pk=None):
        """The customer's acknowledgement: {received_on, received_by, reference}."""
        delivery = self.get_object()
        delivery.record_receipt(request.data.get("received_on"), request.data.get("received_by") or "",
                                request.data.get("reference") or "")
        return Response(self.get_serializer(delivery).data)

    @action(detail=False, methods=["get"])
    def unacknowledged(self, request):
        """Deliveries out that no customer has yet signed for, oldest first."""
        rows = self.filter_queryset(self.get_queryset()).filter(
            posted=True, reverses__isnull=True, received_on__isnull=True).order_by("delivery_date", "pk")
        return Response([{"id": row.pk, "number": row.number, "date": row.delivery_date,
                          "customer": row.sales_order.customer.name, "lr_number": row.lr_number} for row in rows])

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        document = self.get_object()
        response = HttpResponse(document.render_pdf(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{document.number or f"draft-{document.pk}"}.pdf"'
        return response

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """Email the PDF; {"to", "subject", "body"} override the party's address and the wording."""
        document = self.get_object()
        recipient = document.email_to_customer(to=request.data.get("to") or None, subject=request.data.get("subject") or None,
                                 body=request.data.get("body") or None, user=request.user)
        return Response({"sent_to": recipient})

    @action(detail=True, methods=["post"])
    def transport(self, request, pk=None):
        """Who carried it: {transporter, lr_number, lr_date, vehicle_number}, after it shipped too."""
        delivery = self.get_object()
        given = request.data.get("transporter")
        transporter = record_or_404(Party, given, "transporter", optional=True)
        delivery.record_transport(transporter, request.data.get("lr_number") or "",
                                  request.data.get("lr_date") or None, request.data.get("vehicle_number") or "")
        return Response(self.get_serializer(delivery).data)

    @action(detail=True, methods=["post"])
    def post_delivery(self, request, pk=None):
        delivery = self.get_object()
        try:
            delivery.post()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(delivery).data)

    @action(detail=True, methods=["post"])
    def backorder(self, request, pk=None):
        """Raise a draft delivery for whatever this shipment left behind."""
        delivery = self.get_object()
        try:
            created = delivery.create_backorder(
                delivery_date=request.data.get("delivery_date")
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(created).data)

    @action(detail=True, methods=["post"])
    def customer_return(self, request, pk=None):
        """
        Take goods back. Credits the invoices that billed them unless
        {"credit_invoices": false} is passed (a replacement, not a refund).
        {"quantities": {"<delivery_line_id>": "20"}} takes back part.
        """
        delivery = self.get_object()
        credit = flag(request.data, "credit_invoices", True)
        quantities = quantities_by_line(request.data.get("quantities"), delivery.lines.all(), "delivery")
        try:
            returned = delivery.create_return(credit_invoices=credit, quantities=quantities)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        payload = self.get_serializer(returned).data
        payload["credit_notes"] = [
            {"id": note.pk, "number": note.number, "total": str(note.total())}
            for note in returned.credit_notes_created
        ]
        return Response(payload)


class DeliveryLineViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "delivery__sales_order__customer"
    queryset = DeliveryLine.objects.select_related("delivery", "order_line", "warehouse")
    serializer_class = DeliveryLineSerializer


class PriceListViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PriceList.objects.prefetch_related("entries__item")
    serializer_class = PriceListSerializer
    filter_fields = ["is_active", "currency", "is_default"]
    search_fields = ["code", "name"]


class PriceListItemViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PriceListItem.objects.select_related("price_list", "item")
    serializer_class = PriceListItemSerializer
    filter_fields = ["price_list", "item"]
    search_fields = ["item__sku", "item__name"]


class CustomerProfileViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "party"
    queryset = CustomerProfile.objects.select_related("party", "price_list", "sales_rep")
    serializer_class = CustomerProfileSerializer
    filter_fields = ["party", "sales_rep"]

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


class NewCustomerViewSet(NewPartyViewSet):
    """
    A new customer in one go: who they are, where they are, whom to speak
    to, their tax standing and the terms they trade on (NewPartyViewSet).
    The rep who opens the account does not set its credit limit, and the
    GSTIN is the GST Officer's; the rep's own profile is already made by
    their scope (SalesScope.created), and is filled in, not made twice.
    """

    role = PartyRole.CUSTOMER
    ONES = (("tax", PartyTaxProfileSerializer, "accounting.add_partytaxprofile", PartyTaxProfile),
            ("terms", CustomerProfileSerializer, "sales.add_customerprofile", CustomerProfile))


class QuotationViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["number", "reference", "customer__code", "customer__name"]
    filter_fields = ["customer", "status"]
    date_field = "quotation_date"
    ordering_fields = ["quotation_date", "valid_until", "number"]

    queryset = Quotation.objects.select_related("customer").prefetch_related(
        "lines__item", "lines__charge", "lines__taxes")
    serializer_class = QuotationSerializer
    action_permission_map = {"accept": "sales.add_salesorder"}

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        quotation = self.get_object()
        response = HttpResponse(quotation.render_pdf(), content_type="application/pdf")
        name = quotation.number or f"draft-{quotation.pk}"
        response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
        return response

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """Email the quote as a PDF and mark it sent."""
        quotation = self.get_object()
        try:
            recipient = quotation.email_to_customer(
                to=request.data.get("to"),
                subject=request.data.get("subject"),
                body=request.data.get("body"),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response({"sent_to": recipient, "number": quotation.number})

    @action(detail=True, methods=["post"])
    def mark_sent(self, request, pk=None):
        """Record that the quote went out by some other route."""
        quotation = self.get_object()
        try:
            quotation.mark_sent()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(quotation).data)

    @action(detail=True, methods=["post"])
    def revise(self, request, pk=None):
        """Supersede this quote with an editable revision."""
        quotation = self.get_object()
        try:
            revision = quotation.create_revision(
                quotation_date=request.data.get("quotation_date"),
                valid_until=request.data.get("valid_until"),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(revision).data)

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
            # Approving as part of acceptance needs the approval
            # permission, not merely permission to accept a quote.
            approve_as = None
            if request.data.get("approve") and request.user.has_perm("sales.approve_order"):
                approve_as = request.user
            order = quotation.accept(
                order_date=request.data.get("order_date"), approve_as=approve_as
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(SalesOrderSerializer(order).data)


class QuotationLineViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "quotation__customer"
    queryset = QuotationLine.objects.select_related("quotation", "item")
    serializer_class = QuotationLineSerializer


class DunningLevelViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = DunningLevel.objects.all()
    serializer_class = DunningLevelSerializer
    filter_fields = ["is_active"]
    search_fields = ["name"]
    # A run writes to every overdue customer; setting the levels up is not
    # the right to do that.
    action_permission_map = {"run": "sales.post_invoice"}

    @action(detail=False, methods=["post"])
    def run(self, request):
        """
        Raise the reminders now due. {"send": false} records them without
        emailing, which is how you preview a run.
        """
        notices = run_dunning(
            as_of=request.data.get("as_of"),
            send=flag(request.data, "send", True),
        )
        return Response(DunningNoticeSerializer(notices, many=True).data)


class DunningNoticeViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    customer_path = "invoice__customer"
    queryset = DunningNotice.objects.select_related("invoice__customer", "level")
    serializer_class = DunningNoticeSerializer
    filter_fields = ["invoice", "level", "invoice__customer"]
    search_fields = ["invoice__number", "invoice__customer__name", "sent_to"]
    date_field = "sent_at"
    ordering_fields = ["sent_at"]


class CommissionPlanViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = CommissionPlan.objects.all()
    serializer_class = CommissionPlanSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]

    @action(detail=False, methods=["get"])
    def report(self, request):
        """Commission earned per rep over ?from= / ?to=."""
        return Response(
            commission_report(
                date_from=request.query_params.get("from"),
                date_to=request.query_params.get("to"),
            )
        )


class SalesRepViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalesRep.objects.select_related("party", "plan", "team")
    filter_fields = ["is_active", "team"]
    search_fields = ["party__code", "party__name"]
    serializer_class = SalesRepSerializer


class SalesTeamViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalesTeam.objects.select_related("leader__party").annotate(member_count=Count("members"))
    serializer_class = SalesTeamSerializer
    filter_fields = ["is_active", "leader"]
    search_fields = ["code", "name"]
    ordering_fields = ["code", "name"]


class SalesTargetViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Targets, and `report` (?from&to): each against what was posted in its own span."""

    queryset = SalesTarget.objects.select_related("team", "rep__party")
    serializer_class = SalesTargetSerializer
    filter_fields = ["team", "rep"]
    date_field = "period_start"
    ordering_fields = ["period_start", "amount"]

    @action(detail=False, methods=["get"])
    def report(self, request):
        return Response(targets_report(date_from=request.query_params.get("from"),
                                       date_to=request.query_params.get("to")))


class RecurringInvoiceViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = RecurringInvoice.objects.select_related("customer").prefetch_related("lines__taxes")
    serializer_class = RecurringInvoiceSerializer
    filter_fields = ["customer", "is_active", "interval"]
    search_fields = ["code", "customer__name"]
    ordering_fields = ["next_run_date", "code"]
    # Issuing an invoice from the schedule is issuing an invoice; left
    # unmapped, it took only the right to keep schedules.
    action_permission_map = {"generate": "sales.add_invoice", "run": "sales.add_invoice"}

    def _may_post(self, request, schedules):
        # A schedule that posts what it issues posts as whoever runs it.
        if any(schedule.auto_post for schedule in schedules) and not request.user.has_perm("sales.post_invoice"):
            raise PermissionDenied("This schedule posts each invoice it issues, which takes the right to post "
                                   "invoices.")

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        """Issue the next invoice in this series."""
        schedule = self.get_object()
        self._may_post(request, [schedule])
        try:
            invoice = schedule.generate_one(on_date=request.data.get("on_date"),
                                            expected=request.data.get("next_run_date") or None)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(InvoiceSerializer(invoice).data)

    @action(detail=False, methods=["post"])
    def run(self, request):
        """Issue every invoice now due across all active schedules."""
        as_of = to_date(request.data.get("as_of")) or timezone.localdate()
        self._may_post(request, [s for s in RecurringInvoice.objects.filter(is_active=True, auto_post=True)
                                 if s.next_run_date and s.next_run_date <= as_of and not s.has_finished()])
        issued = generate_due_invoices(as_of=as_of)
        return Response(InvoiceSerializer(issued, many=True).data)


class RecurringInvoiceLineViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "schedule__customer"
    queryset = RecurringInvoiceLine.objects.select_related("schedule", "item")
    serializer_class = RecurringInvoiceLineSerializer
    filter_fields = ["schedule"]


class SalesReportViewSet(viewsets.ViewSet):
    """Bad debt and the statement run, neither of which was reachable."""

    permission_classes = [IsAuthenticated, RequiredPermission, ActionPermission]

    required_permission = "sales.view_invoice"

    action_permission_map = {"statements": "sales.post_invoice"}

    def list(self, request):
        return Response({"bad-debt": "bad-debt/", "statements": "statements/"})

    @action(detail=False, methods=["get"], url_path="bad-debt")
    def bad_debt(self, request):
        rows = bad_debt_report(
            start=request.query_params.get("start"),
            end=request.query_params.get("end"),
        )
        if isinstance(rows, dict):
            return Response({
                key: str(value) if not isinstance(value, (list, int, float)) else value
                for key, value in rows.items()
            })
        return Response([
            {**row, "customer": str(row.get("customer", ""))} for row in rows
        ])

    @action(detail=False, methods=["post"])
    def statements(self, request):
        """
        Send everybody their statement. A POST, because it sends email —
        a report you can refresh by reloading should not be one.
        """
        sent = send_statements(as_of=request.data.get("as_of"))
        return Response({"sent": len(sent) if sent is not None else 0})


class ThirdPartyReleaseViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """An agency's inspection certificate, entered and posted in one step."""

    queryset = ThirdPartyRelease.objects.select_related("customer", "agency", "sales_order").prefetch_related(
        "lines__lot")
    serializer_class = ThirdPartyReleaseSerializer
    filter_fields = ["customer", "sales_order", "posted", "agency"]
    search_fields = ["number", "their_reference", "customer__name"]
    date_field = "inspected_on"
    ordering_fields = ["inspected_on", "number"]

    @action(detail=False, methods=["post"])
    def record(self, request):
        from django.db import transaction

        data = request.data
        rows = data.get("lines") or []
        try:
            with transaction.atomic():
                release = ThirdPartyRelease.objects.create(
                    customer_id=data.get("customer"), agency_id=data.get("agency"),
                    sales_order_id=data.get("sales_order") or None,
                    inspector=data.get("inspector") or "",
                    their_reference=data.get("their_reference") or "",
                    inspected_on=data.get("inspected_on"),
                )
                for row in rows:
                    ThirdPartyReleaseLine.objects.create(
                        release=release, lot_id=row.get("lot"),
                        quantity_offered=Decimal(str(row.get("quantity_offered"))),
                        quantity_released=Decimal(str(row.get("quantity_released"))),
                        remarks=row.get("remarks") or "",
                    )
                release.post()
        except (DjangoValidationError, InvalidOperation, TypeError) as exc:
            messages = getattr(exc, "messages", None) or ["Quantities are numbers."]
            raise DRFValidationError(messages)
        except IntegrityError:
            raise DRFValidationError(["Customer, agency, date and each line's batch and "
                                      "quantities are needed, and passed cannot exceed "
                                      "offered."])
        return Response(self.get_serializer(release).data, status=201)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        release = self.get_object()
        try:
            release.void(request.data.get("reason", ""))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(release).data)

    @action(detail=False, methods=["get"])
    def coverage(self, request):
        """?customer=&lot=: released, shipped and spare for one batch."""
        from apps.core.models import Party
        from apps.inventory.models import Lot

        from .third_party import coverage

        customer = get_object_or_404(Party, pk=request.query_params.get("customer"))
        lot = get_object_or_404(Lot, pk=request.query_params.get("lot"))
        found = coverage(customer, lot)

        def text(value):
            # normalize() for 450 rather than 450.0000; format() so it never
            # comes back as 4.5E+2.
            return format(value.normalize(), "f")

        return Response({
            **found,
            "released_for_any_order": text(found["released_for_any_order"]),
            "released_for_orders": {str(k): text(v)
                                    for k, v in found["released_for_orders"].items()},
            "net_shipped_by_order": {str(k): text(v)
                                     for k, v in found["net_shipped_by_order"].items()},
            "spare_for_any_order": text(found["spare_for_any_order"]),
        })
