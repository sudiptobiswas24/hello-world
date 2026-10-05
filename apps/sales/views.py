from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import viewsets

from decimal import Decimal, InvalidOperation

from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import ActionPermission, RequiredPermission
from rest_framework.response import Response

from apps.core.api import flag

from apps.accounting.defaults import chosen_or_default
from apps.core.audit import AuditableViewSetMixin

from django.http import HttpResponse

from django.db.models import Prefetch

from .models import (
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
    revenue_report,
    run_dunning,
)
from .serializers import (
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
    return SalesOrderLine.objects.select_related("item", "uom").prefetch_related(
        "taxes",
        Prefetch("delivery_lines", queryset=DeliveryLine.objects.select_related("delivery")),
        Prefetch("invoice_lines", queryset=InvoiceLine.objects.select_related("invoice")),
    )


class SalesOrderViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["number", "reference", "customer__code", "customer__name"]
    filter_fields = ["customer", "status", "sales_rep"]
    date_field = "order_date"
    ordering_fields = ["order_date", "number"]

    queryset = SalesOrder.objects.select_related(
        "customer__tax_profile", "currency", "payment_terms"
    ).prefetch_related(Prefetch("lines", queryset=_order_lines()), "supplied_items")
    serializer_class = SalesOrderSerializer
    action_permission_map = {
        "create_invoice": "sales.add_invoice",
        "down_payment": "sales.add_invoice",
        "approve": "sales.approve_order",
        "confirm": "sales.change_salesorder",
        "cancel": "sales.change_salesorder",
    }

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


class SalesOrderLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = _order_lines().select_related("order__customer__tax_profile")
    serializer_class = SalesOrderLineSerializer
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


class SuppliedItemViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """What the customer sends for a job-work order; settled once it is confirmed."""

    queryset = SuppliedItem.objects.select_related("order", "item")
    serializer_class = SuppliedItemSerializer
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


class InvoiceViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["number", "reference", "customer__code", "customer__name", "sales_order__number"]
    filter_fields = ["customer", "posted", "credits", "credits__isnull", "is_down_payment",
                     "sales_order"]
    date_field = "invoice_date"
    ordering_fields = ["invoice_date", "due_date", "number"]

    queryset = Invoice.objects.select_related(
        "customer__tax_profile", "currency", "payment_terms", "credits"
    ).prefetch_related(*INVOICE_FIGURES)
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
    queryset = InvoiceLine.objects.select_related(
        "invoice__customer__tax_profile", "item", "credits_line"
    ).prefetch_related("taxes", "recorded_taxes__tax")
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
    search_fields = ["number", "reference", "sales_order__number", "sales_order__customer__code", "sales_order__customer__name"]
    filter_fields = ["sales_order", "posted", "reverses"]
    date_field = "delivery_date"
    ordering_fields = ["delivery_date", "number"]

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
        quantities = None
        requested = request.data.get("quantities")
        if requested:
            # {"<delivery_line_id>": "20"}: part of it back.
            lines = {str(line.pk): line for line in delivery.lines.all()}
            try:
                quantities = {lines[str(line_id)]: Decimal(str(quantity))
                              for line_id, quantity in requested.items()}
            except KeyError as exc:
                raise DRFValidationError(f"Line {exc} is not on this delivery.")
            except (InvalidOperation, TypeError, AttributeError):
                raise DRFValidationError("Quantities must be numbers, by delivery line.")
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
    search_fields = ["number", "reference", "customer__code", "customer__name"]
    filter_fields = ["customer", "status"]
    date_field = "quotation_date"
    ordering_fields = ["quotation_date", "valid_until", "number"]

    queryset = Quotation.objects.prefetch_related("lines")
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


class QuotationLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = QuotationLine.objects.select_related("quotation", "item")
    serializer_class = QuotationLineSerializer


class DunningLevelViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = DunningLevel.objects.all()
    serializer_class = DunningLevelSerializer
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


class DunningNoticeViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = DunningNotice.objects.select_related("invoice", "level")
    serializer_class = DunningNoticeSerializer


class CommissionPlanViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = CommissionPlan.objects.all()
    serializer_class = CommissionPlanSerializer

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
    queryset = SalesRep.objects.select_related("party", "plan")
    serializer_class = SalesRepSerializer


class RecurringInvoiceViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = RecurringInvoice.objects.prefetch_related("lines")
    serializer_class = RecurringInvoiceSerializer
    action_permission_map = {"run": "sales.add_invoice"}

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        """Issue the next invoice in this series."""
        schedule = self.get_object()
        try:
            invoice = schedule.generate_one(on_date=request.data.get("on_date"))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(InvoiceSerializer(invoice).data)

    @action(detail=False, methods=["post"])
    def run(self, request):
        """Issue every invoice now due across all active schedules."""
        issued = generate_due_invoices(as_of=request.data.get("as_of"))
        return Response(InvoiceSerializer(issued, many=True).data)


class RecurringInvoiceLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = RecurringInvoiceLine.objects.select_related("schedule", "item")
    serializer_class = RecurringInvoiceLineSerializer


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


class ThirdPartyReleaseViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """An agency's inspection certificate, entered and posted in one step."""

    queryset = ThirdPartyRelease.objects.select_related("customer", "agency").prefetch_related(
        "lines")
    serializer_class = ThirdPartyReleaseSerializer

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
