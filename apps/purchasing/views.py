from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .models import (
    Bill,
    BillLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    ap_aging,
    billed_not_held,
    consignment_on_hand,
    draw_consignment,
    payment_run,
    raise_reorder_requisition,
    reorder_suggestions,
    vendor_balance,
    vendor_performance,
)
from .serializers import (
    BillLineSerializer,
    BillSerializer,
    GoodsReceiptLineSerializer,
    GoodsReceiptSerializer,
    PurchaseOrderLineSerializer,
    PurchaseOrderSerializer,
)


class PurchaseOrderViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PurchaseOrder.objects.prefetch_related("lines")
    serializer_class = PurchaseOrderSerializer
    action_permission_map = {"create_bill": "purchasing.add_bill"}

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        order = self.get_object()
        try:
            order.confirm()
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
        try:
            bill = order.create_bill(
                request.data["payable_account"],
                bill_date=request.data.get("bill_date"),
                reference=request.data.get("reference", ""),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(BillSerializer(bill).data)


class PurchaseOrderLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PurchaseOrderLine.objects.all()
    serializer_class = PurchaseOrderLineSerializer


class BillViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Bill.objects.prefetch_related("lines")
    serializer_class = BillSerializer
    action_permission_map = {
        "post_bill": "purchasing.post_bill",
        "debit_note": "purchasing.post_bill",
    }

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
        bill = self.get_object()
        try:
            debit_note = bill.create_debit_note(memo=request.data.get("memo", ""))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(debit_note).data)


class BillLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BillLine.objects.all()
    serializer_class = BillLineSerializer


class GoodsReceiptViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = GoodsReceipt.objects.prefetch_related("lines")
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
        receipt = self.get_object()
        try:
            return_receipt = receipt.create_return()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(return_receipt).data)


class GoodsReceiptLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = GoodsReceiptLine.objects.all()
    serializer_class = GoodsReceiptLineSerializer


def _run(callable_, *args, **kwargs):
    """Let a model's refusal reach the caller as the sentence it wrote."""
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


class PurchasingReportViewSet(viewsets.ViewSet):
    """
    The aging, the scorecard and the payment run. All derived from
    documents, and none of them reachable.
    """

    permission_classes = [IsAuthenticated]

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
        return Response([
            {**row, "item": str(row.get("item", ""))} for row in billed_not_held()
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
            {**row, "vendor": str(row.get("vendor", "")), "bill": str(row.get("bill", ""))}
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


def _serialise_aging(report):
    if isinstance(report, dict):
        return {
            key: [
                {**row, "vendor": str(row.get("vendor", "")), "bill": str(row.get("bill", ""))}
                for row in value
            ] if isinstance(value, list) else value
            for key, value in report.items()
        }
    return [
        {**row, "vendor": str(row.get("vendor", "")), "bill": str(row.get("bill", ""))}
        for row in report
    ]
