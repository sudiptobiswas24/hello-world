from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated

from apps.accounting.defaults import chosen_or_default
from apps.core.api import flag, money_amount, quantities_by_line, record_or_404
from apps.inventory.models import Warehouse
from apps.core.permissions import ActionPermission, RequiredPermission
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from django.db.models import Prefetch

from .models import (
    BILL_FIGURES,
    Bill,
    BillPayment,
    BillLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    ap_aging,
    billed_not_held,
    bills_still_owed,
    consignment_on_hand,
    draw_consignment,
    orders_to_receive,
    payment_run,
    raise_reorder_requisition,
    reorder_suggestions,
    vendor_balance,
    vendor_performance,
    with_line_figures,
)
from .serializers import (
    BillPaymentSerializer,
    BillLineSerializer,
    BillSerializer,
    GoodsReceiptLineSerializer,
    GoodsReceiptSerializer,
    PurchaseOrderLineSerializer,
    PurchaseOrderSerializer,
)


def _order_lines():
    """Order lines with what their received and billed quantities read."""
    return with_line_figures(
        PurchaseOrderLine.objects.select_related("item", "uom", "charge").prefetch_related(
            "taxes", "components")
    )


class PurchaseOrderViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    extra_params = ('to_receive',)
    search_fields = ["number", "reference", "vendor__code", "vendor__name"]
    filter_fields = ["vendor", "status"]
    date_field = "order_date"
    ordering_fields = ["order_date", "number"]

    queryset = PurchaseOrder.objects.select_related(
        "vendor__tax_profile", "currency"
    ).prefetch_related(Prefetch("lines", queryset=_order_lines()))
    serializer_class = PurchaseOrderSerializer
    action_permission_map = {
        "receive": "purchasing.add_goodsreceipt",
        "approve": "purchasing.approve_purchaseorder",
        "create_bill": "purchasing.add_bill",
        "prepayment": "purchasing.add_bill",
        "confirm": "purchasing.change_purchaseorder",
        "cancel": "purchasing.change_purchaseorder",
    }

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        # ?to_receive=true: confirmed, with goods still to come.
        if flag(self.request.query_params, "to_receive", False):
            queryset = queryset.filter(pk__in=orders_to_receive(queryset))
        return queryset

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        """{receipt_date?, warehouse?}: a draft receipt of what is still to come."""
        order = self.get_object()
        warehouse = request.data.get("warehouse")
        receipt = order.create_receipt(
            receipt_date=request.data.get("receipt_date"),
            warehouse=record_or_404(Warehouse, warehouse, "warehouse") if warehouse else None,
        )
        return Response(GoodsReceiptSerializer(receipt).data, status=201)

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
            "approved_by": str(order.approved_by) if order.approved_by_id else None,
            "approved_at": order.approved_at,
        })

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """
        Sign off what breaches the policy. Who may sign for how much is the
        order's own check (its tiers), not this view's. The mirror of sales:
        purchasing had the permission and the tiers and no way to use them.
        """
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
    def create_bill(self, request, pk=None):
        order = self.get_object()
        account = chosen_or_default(request.data, "payable_account", "payable")
        try:
            bill = order.create_bill(
                account,
                bill_date=request.data.get("bill_date"),
                reference=request.data.get("reference", ""),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(BillSerializer(bill).data)

    @action(detail=True, methods=["post"])
    def prepayment(self, request, pk=None):
        """
        {payable_account?, amount | percent, bill_date?, description?}: a
        draft prepayment bill, the vendor asking for money up front.
        """
        from decimal import Decimal, InvalidOperation

        order = self.get_object()
        account = chosen_or_default(request.data, "payable_account", "payable")
        figures = {}
        for name in ("amount", "percent"):
            value = request.data.get(name)
            if value not in (None, ""):
                try:
                    figures[name] = Decimal(str(value))
                except InvalidOperation:
                    raise DRFValidationError(f"{name} must be a number.")
        try:
            bill = order.create_prepayment_bill(
                account,
                bill_date=request.data.get("bill_date"),
                description=request.data.get("description", ""),
                **figures,
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(BillSerializer(bill).data, status=201)


class PurchaseOrderLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = _order_lines().select_related("order__vendor__tax_profile")
    serializer_class = PurchaseOrderLineSerializer
    action_permission_map = {"close_short": "purchasing.change_purchaseorder",
                             "reopen": "purchasing.change_purchaseorder"}

    @action(detail=True, methods=["post"], url_path="close-short")
    def close_short(self, request, pk=None):
        """{reason}: the vendor will send no more of this line."""
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


class BillViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    extra_params = ('open',)
    search_fields = ["number", "reference", "vendor__code", "vendor__name", "purchase_order__number"]
    filter_fields = ["vendor", "posted", "debits", "debits__isnull", "is_prepayment",
                     "purchase_order", "payable_account", "currency"]
    date_field = "bill_date"
    ordering_fields = ["bill_date", "due_date", "number"]

    queryset = Bill.objects.select_related(
        "vendor__tax_profile", "currency", "payment_terms", "debits"
    ).prefetch_related(*BILL_FIGURES, "lines__item", "lines__charge")
    serializer_class = BillSerializer
    action_permission_map = {
        "post_bill": "purchasing.post_bill",
        "debit_note": "purchasing.post_bill",
        "debit_old_supply": "purchasing.post_bill",
    }

    @action(detail=True, methods=["post"])
    def debit_old_supply(self, request, pk=None):
        """
        {lines: [{item?, description, quantity, unit_price, taxes, expense_account}],
        memo?, on_date?, old_value?}: a debit note with GST on a bill the old
        system booked, which takes the input tax back.
        """
        from apps.accounting.old_supply import from_request
        from apps.inventory.models import Item

        bill = self.get_object()
        lines, memo, on_date, old_value = from_request(request.data, "expense_account", Item)
        try:
            note = bill.debit_old_supply(lines, memo=memo, on_date=on_date, old_value=old_value)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(note).data, status=201)

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        # ?open=true: posted bills that still owe the vendor, as amount_due()
        # reckons it.
        if flag(self.request.query_params, "open", False):
            queryset = bills_still_owed(queryset)
        return queryset

    @action(detail=True, methods=["post"])
    def post_bill(self, request, pk=None):
        bill = self.get_object()
        try:
            bill.post(memo=request.data.get("memo"))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(bill).data)

    @action(detail=True, methods=["post"])
    def debit_note(self, request, pk=None):
        """
        Debit the whole bill, or pass {"quantities": {"<bill_line_id>": "3"}}
        to give back part of it.
        """
        bill = self.get_object()
        quantities = quantities_by_line(request.data.get("quantities"), bill.lines.all(), "bill")
        amount = money_amount(request.data, "amount")  # a prepayment's, part of what is left
        try:
            debit_note = bill.create_debit_note(memo=request.data.get("memo", ""),
                                                quantities=quantities, amount=amount)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(debit_note).data)


class BillPaymentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    Which bill a payment settles. Only the admin could say before review,
    while the sales side had always had its mirror over the API.
    """

    filter_fields = ["bill", "payment"]

    queryset = BillPayment.objects.select_related("bill", "payment")
    serializer_class = BillPaymentSerializer

    def perform_create(self, serializer):
        _run(super().perform_create, serializer)


class BillLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BillLine.objects.select_related(
        "bill__vendor__tax_profile", "item", "debits_line"
    ).prefetch_related("taxes", "recorded_taxes__tax")
    serializer_class = BillLineSerializer


class GoodsReceiptViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["number", "reference", "purchase_order__number", "purchase_order__vendor__code", "purchase_order__vendor__name"]
    filter_fields = ["purchase_order", "posted", "reverses", "reverses__isnull"]
    date_field = "receipt_date"
    ordering_fields = ["receipt_date", "number"]

    queryset = GoodsReceipt.objects.select_related("purchase_order__vendor").prefetch_related(
        Prefetch("lines", queryset=GoodsReceiptLine.objects.select_related(
            "order_line__item", "order_line__charge", "lot")))
    serializer_class = GoodsReceiptSerializer
    action_permission_map = {
        "post_receipt": "purchasing.post_goodsreceipt",
        "return_receipt": "purchasing.post_goodsreceipt",
    }

    @action(detail=True, methods=["post"])
    def post_receipt(self, request, pk=None):
        receipt = self.get_object()
        try:
            receipt.post()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(receipt).data)

    @action(detail=True, methods=["post"])
    def return_receipt(self, request, pk=None):
        """
        Send goods back. Debits the bills that charged for them unless
        {"debit_bills": false} (a replacement is coming, not a refund);
        {"quantities": {"<receipt_line_id>": "20"}} sends back part.
        """
        receipt = self.get_object()
        quantities = quantities_by_line(request.data.get("quantities"), receipt.lines.all(), "receipt")
        debit = flag(request.data, "debit_bills", True)
        try:
            return_receipt = receipt.create_return(quantities=quantities, debit_bills=debit)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(return_receipt).data)


def _run(callable_, *args, **kwargs):
    """Let a model's refusal reach the caller as the sentence it wrote."""
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


class ReceiptRoutingMixin:
    """
    Walking a receipt line along its route.

    An action rather than a writable field: moving goods from the bay to
    the shelf writes stock movements, and a PATCH that moved stock would
    move it again on every retry.
    """

    action_permission_map = {"advance": "purchasing.change_goodsreceipt"}

    @action(detail=True, methods=["post"], url_path="advance")
    def advance(self, request, pk=None):
        line = self.get_object()
        move = _run(line.advance, quantity=request.data.get("quantity"))
        return Response({
            "from": move.from_warehouse.code,
            "to": move.to_warehouse.code,
            "quantity": move.quantity,
            "transfer": move.transfer.number,
            "still_here": line.quantity_at(move.from_warehouse),
        })

    @action(detail=True, methods=["get"], url_path="route")
    def route(self, request, pk=None):
        """Where this line's goods are, and where they still have to go."""
        line = self.get_object()
        return Response({
            "destination": line.warehouse.code,
            "arrived_at": line.arrived_at().code,
            "current_step": line.current_step().code,
            "steps": [
                {"warehouse": step.code, "quantity": line.quantity_at(step)}
                for step in line.route_steps() if step is not None
            ],
        })


class GoodsReceiptLineViewSet(ReceiptRoutingMixin, AuditableViewSetMixin,
                             viewsets.ModelViewSet):
    queryset = GoodsReceiptLine.objects.all()
    serializer_class = GoodsReceiptLineSerializer


class PurchasingReportViewSet(viewsets.ViewSet):
    """
    The aging, the scorecard and the payment run. All derived from
    documents, and none of them reachable.
    """

    permission_classes = [IsAuthenticated, RequiredPermission, ActionPermission]

    required_permission = "purchasing.view_purchaseorder"

    action_permission_map = {"draw": "purchasing.post_bill", "raise_reorder": "purchasing.add_purchaserequisition"}

    def list(self, request):
        return Response({
            "aging": "aging/",
            "vendor-performance": "vendor-performance/",
            "billed-not-held": "billed-not-held/",
            "reorder": "reorder/",
            "consignment": "consignment/",
            "payment-run": "payment-run/",
            "vendor-balance": "vendor-balance/",
        })

    @action(detail=False, methods=["get"])
    def aging(self, request):
        return Response(_serialise_aging(ap_aging(as_of=request.query_params.get("as_of"))))

    @action(detail=False, methods=["get"], url_path="vendor-performance")
    def performance(self, request):
        rows = vendor_performance(
            start=request.query_params.get("start"),
            end=request.query_params.get("end"),
        )
        return Response([
            {**row, "vendor": str(row["vendor"])} for row in rows
        ])

    @action(detail=False, methods=["get"], url_path="billed-not-held")
    def billed_not_held_report(self, request):
        # Each field named, not the row passed through: the row carries
        # the order, the vendor and the line, and passing it through
        # crashed on the first one (every test had asked with none).
        return Response([
            {
                "order": row["order"].number, "order_id": row["order"].pk,
                "vendor": str(row["vendor"]), "line_id": row["line"].pk,
                "item": str(row["item"]) if row["item"] else "",
                "quantity": str(row["quantity"]), "value": str(row["value"]),
            }
            for row in billed_not_held()
        ])

    @action(detail=False, methods=["get"])
    def reorder(self, request):
        return Response([
            {
                "item": str(row["item"]),
                "warehouse": str(row["warehouse"]),
                "on_hand": row["on_hand"],
                "shortfall": row.get("shortfall"),
                "suggested": row.get("suggested"),
            }
            for row in reorder_suggestions()
        ])

    @action(detail=False, methods=["get"])
    def consignment(self, request):
        return Response([
            {
                "item": str(row["item"]),
                "warehouse": str(row["warehouse"]),
                "vendor": str(row["vendor"]),
                "quantity": row["quantity"],
            }
            for row in consignment_on_hand()
        ])

    @action(detail=False, methods=["get"], url_path="payment-run")
    def payment_run_report(self, request):
        """
        What is due, so somebody can decide to pay it. A GET: proposing a
        payment run is not making one.
        """
        rows = payment_run(due_by=request.query_params.get("due_by"))
        return Response([
            {
                "vendor": str(row["vendor"]), "vendor_id": row["vendor"].pk,
                "currency": row["currency"].code if row["currency"] else None,
                "total": str(row["total"]),
                "bills": [_bill_row(entry) for entry in row["bills"]],
            }
            for row in rows
        ])


    @action(detail=False, methods=["get"], url_path="vendor-balance")
    def balance(self, request):
        """
        A vendor's net position, signed so one who has been overpaid
        reads negative rather than silently as zero.
        """
        from apps.core.models import Party

        vendor_id = request.query_params.get("vendor")
        if not vendor_id:
            raise DRFValidationError("vendor is required.")
        vendor = get_object_or_404(Party, pk=vendor_id)
        return Response({"vendor": str(vendor), "balance": vendor_balance(vendor)})

    @action(detail=False, methods=["post"], url_path="draw-consignment")
    def draw(self, request):
        """
        Take consigned stock into ownership and raise the bill for it.
        A POST because it buys goods; a GET that bought things would be
        bought again by every refresh.
        """
        from apps.accounting.models import Account
        from apps.inventory.models import Item, Warehouse

        required = ("item", "from_warehouse", "to_warehouse", "quantity",
                    "payable_account")
        missing = [field for field in required if request.data.get(field) is None]
        if missing:
            raise DRFValidationError(f"{', '.join(missing)} are required.")
        order, bill = _run(
            draw_consignment,
            item=get_object_or_404(Item, pk=request.data["item"]),
            from_warehouse=get_object_or_404(
                Warehouse, pk=request.data["from_warehouse"]
            ),
            to_warehouse=get_object_or_404(Warehouse, pk=request.data["to_warehouse"]),
            quantity=request.data["quantity"],
            payable_account=get_object_or_404(
                Account, pk=request.data["payable_account"]
            ),
            unit_price=request.data.get("unit_price"),
            on_date=request.data.get("on_date"),
        )
        return Response({"order": order.number, "bill": bill.number})

    @action(detail=False, methods=["post"], url_path="raise-reorder-requisition")
    def raise_reorder(self, request):
        """
        Turn the suggestions into a requisition somebody has to approve.
        A requisition and not an order: a rule firing on stale data
        should cost a conversation, not a delivery.
        """
        from apps.inventory.models import Warehouse

        warehouse_id = request.data.get("warehouse")
        requisition = _run(
            raise_reorder_requisition,
            requested_by=request.user,
            warehouse=(
                get_object_or_404(Warehouse, pk=warehouse_id) if warehouse_id else None
            ),
            on_date=request.data.get("on_date"),
        )
        if requisition is None:
            return Response({"requisition": None, "reason": "nothing to reorder"})
        return Response({"requisition": str(requisition)})


def _bill_row(entry):
    bill = entry["bill"]
    return {
        "id": bill.pk, "number": bill.number, "reference": bill.reference,
        "vendor": str(bill.vendor), "due_date": entry["due_date"],
        "days_overdue": entry["days_overdue"], "amount_due": str(entry["amount_due"]),
    }


def _serialise_aging(report):
    """
    ap_aging()'s buckets, each with its bills. This once flattened rows of
    an older shape, and crashed on the first outstanding bill: every test
    had asked it with nothing owed.
    """
    return {
        key: {"count": bucket["count"], "total": str(bucket["total"]),
              "bills": [_bill_row(entry) for entry in bucket["bills"]]}
        for key, bucket in report.items()
    }
