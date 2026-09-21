"""
The API for stock.

Warehouses, items and raw movements were exposed. Adjustments, counts,
transfers, lots, bins and reservations were not — all of them built,
tested, and reachable only from a Python shell.

Posting, voiding, dispatching and cancelling are actions rather than
writable fields. A status somebody can PATCH is not a state machine, and
the models refuse those edits anyway; exposing them as fields would only
turn a considered refusal into a confusing one.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .models import (
    AdjustmentReason,
    Item,
    Lot,
    StockAdjustment,
    StockAdjustmentLine,
    StockCount,
    StockCountLine,
    StockMovement,
    StockReservation,
    StockTransfer,
    StockTransferLine,
    StorageBin,
    Warehouse,
    movement_summary,
    negative_stock,
    reconcile_to_ledger,
    slow_moving,
    stock_aging,
    stock_ledger,
    stock_valuation,
)
from .serializers import (
    AdjustmentReasonSerializer,
    ItemSerializer,
    LotSerializer,
    StockAdjustmentLineSerializer,
    StockAdjustmentSerializer,
    StockCountLineSerializer,
    StockCountSerializer,
    StockMovementSerializer,
    StockReservationSerializer,
    StockTransferLineSerializer,
    StockTransferSerializer,
    StorageBinSerializer,
    WarehouseSerializer,
)
from .tracking import expiring, traceability


def _run(callable_, *args, **kwargs):
    """Let a model's refusal reach the caller as the sentence it wrote."""
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


class WarehouseViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer


class ItemViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Item.objects.all()
    serializer_class = ItemSerializer
    action_permission_map = {"set_standard_cost": "inventory.change_item"}

    @action(detail=True, methods=["get"])
    def stock(self, request, pk=None):
        """Where this item is, and what it is worth."""
        item = self.get_object()
        report = stock_valuation(
            as_of=request.query_params.get("as_of"), item=item
        )
        return Response({
            "total_value": report["total_value"],
            "rows": [
                {
                    "warehouse": row["warehouse"].code,
                    "quantity": row["quantity"],
                    "value": row["value"],
                    "unit_cost": row["unit_cost"],
                }
                for row in report["rows"]
            ],
        })

    @action(detail=True, methods=["get"])
    def ledger(self, request, pk=None):
        item = self.get_object()
        report = _run(
            stock_ledger, item,
            start=request.query_params.get("start"),
            end=request.query_params.get("end"),
        )
        return Response({
            "opening": report["opening"],
            "closing": report["closing"],
            "rows": [
                {
                    "date": row["date"],
                    "type": row["type"],
                    "warehouse": row["warehouse"].code,
                    "lot": row["lot"].code if row["lot"] else None,
                    "bin": row["bin"].code if row["bin"] else None,
                    "quantity": row["quantity"],
                    "balance": row["balance"],
                    "reference": row["reference"],
                }
                for row in report["rows"]
            ],
        })

    @action(detail=True, methods=["post"])
    def set_standard_cost(self, request, pk=None):
        """
        Changing a standard revalues the stock on hand, which is a
        posting — so it is an action and not a writable field.
        """
        item = self.get_object()
        amount = request.data.get("standard_cost")
        if amount is None:
            raise DRFValidationError("standard_cost is required.")
        reason_id = request.data.get("reason")
        reason = get_object_or_404(AdjustmentReason, pk=reason_id) if reason_id else None
        from .models import set_standard_cost

        raised = _run(
            set_standard_cost, item, amount,
            on_date=request.data.get("on_date"), reason=reason,
        )
        return Response({
            "item": self.get_serializer(item).data,
            "revaluations": [adjustment.number for adjustment in raised],
        })


class StockMovementViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StockMovement.objects.select_related("item", "warehouse", "lot", "bin")
    serializer_class = StockMovementSerializer


class LotViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Lot.objects.select_related("item")
    serializer_class = LotSerializer

    @action(detail=True, methods=["get"])
    def trail(self, request, pk=None):
        """Everywhere this batch has been — the question a recall asks."""
        lot = self.get_object()
        return Response([
            {
                "date": row["occurred_at"],
                "warehouse": row["warehouse"].code,
                "type": row["type"],
                "quantity": row["quantity"],
                "reference": row["reference"],
            }
            for row in traceability(lot)
        ])

    @action(detail=False, methods=["get"])
    def expiring(self, request):
        before = request.query_params.get("before")
        if not before:
            raise DRFValidationError("before is required, as a date.")
        return Response([
            {
                "lot": row["lot"].code,
                "item": row["item"].sku,
                "quantity": row["quantity"],
                "expires_on": row["expires_on"],
                "expired": row["expired"],
            }
            for row in _run(expiring, before)
        ])


class StorageBinViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StorageBin.objects.select_related("warehouse", "parent")
    serializer_class = StorageBinSerializer


class AdjustmentReasonViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = AdjustmentReason.objects.all()
    serializer_class = AdjustmentReasonSerializer


class StockAdjustmentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StockAdjustment.objects.prefetch_related("lines")
    serializer_class = StockAdjustmentSerializer
    action_permission_map = {
        "post": "inventory.change_stockadjustment",
        "void": "inventory.change_stockadjustment",
    }

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        adjustment = self.get_object()
        _run(adjustment.post, memo=request.data.get("memo", ""))
        return Response(self.get_serializer(adjustment).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        adjustment = self.get_object()
        _run(
            adjustment.void,
            on_date=request.data.get("on_date"), memo=request.data.get("memo", ""),
        )
        return Response(self.get_serializer(adjustment).data)


class StockAdjustmentLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StockAdjustmentLine.objects.select_related("item", "adjustment")
    serializer_class = StockAdjustmentLineSerializer


class StockCountViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StockCount.objects.prefetch_related("lines")
    serializer_class = StockCountSerializer
    action_permission_map = {"post": "inventory.change_stockcount"}

    @action(detail=True, methods=["post"])
    def add(self, request, pk=None):
        """
        Put an item on the sheet, freezing what the books say right now —
        which is the whole point of the document and cannot be done by
        POSTing a line with a system quantity of the caller's choosing.
        """
        count = self.get_object()
        item = get_object_or_404(Item, pk=request.data.get("item"))
        counted = request.data.get("counted_quantity")
        if counted is None:
            raise DRFValidationError("counted_quantity is required.")
        lot_id = request.data.get("lot")
        bin_id = request.data.get("bin")
        line = _run(
            count.add, item, counted,
            lot=get_object_or_404(Lot, pk=lot_id) if lot_id else None,
            storage_bin=get_object_or_404(StorageBin, pk=bin_id) if bin_id else None,
        )
        return Response(StockCountLineSerializer(line).data)

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        count = self.get_object()
        adjustment = _run(count.post, memo=request.data.get("memo", ""))
        return Response({
            "count": self.get_serializer(count).data,
            "adjustment": (
                StockAdjustmentSerializer(adjustment).data if adjustment else None
            ),
        })


class StockCountLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StockCountLine.objects.select_related("item", "count")
    serializer_class = StockCountLineSerializer


class StockTransferViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StockTransfer.objects.prefetch_related("lines")
    serializer_class = StockTransferSerializer
    action_permission_map = {
        "post": "inventory.change_stocktransfer",
        "send": "inventory.change_stocktransfer",
        "receive": "inventory.change_stocktransfer",
        "cancel": "inventory.change_stocktransfer",
    }

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        transfer = self.get_object()
        _run(transfer.post)
        return Response(self.get_serializer(transfer).data)

    # Not named dispatch(): that is View.dispatch, the method that routes
    # every request on this viewset, and overriding it sent each of them
    # into the despatch action instead. The URL is still /dispatch/.
    @action(detail=True, methods=["post"], url_path="dispatch")
    def send(self, request, pk=None):
        transfer = self.get_object()
        _run(transfer.dispatch)
        return Response(self.get_serializer(transfer).data)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        """
        `quantities` is {line id: quantity} for a lorry that arrived with
        less than it left with. Omit it and everything still in transit
        arrives.
        """
        transfer = self.get_object()
        supplied = request.data.get("quantities")
        quantities = None
        if supplied:
            quantities = {
                get_object_or_404(StockTransferLine, pk=line_id, transfer=transfer): amount
                for line_id, amount in supplied.items()
            }
        _run(transfer.receive, quantities=quantities)
        return Response(self.get_serializer(transfer).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        transfer = self.get_object()
        _run(transfer.cancel, memo=request.data.get("memo", ""))
        return Response(self.get_serializer(transfer).data)


class StockTransferLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StockTransferLine.objects.select_related("item", "transfer")
    serializer_class = StockTransferLineSerializer


class StockReservationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only on purpose. A reservation is made and released by the
    document that holds it — a claim somebody can POST out of thin air
    is a claim nothing will ever give back.
    """

    queryset = StockReservation.objects.select_related("item", "warehouse")
    serializer_class = StockReservationSerializer


class StockReportViewSet(viewsets.ViewSet):
    """Reading the stock ledger back. Every one of these is read-only."""

    # DjangoModelPermissions needs a queryset to derive a permission from,
    # and a report is not a model. Reading one is allowed to anybody who
    # may see stock at all; what they must not do is change it, and there
    # is nothing here that can.
    permission_classes = [IsAuthenticated]

    def list(self, request):
        return Response({
            "valuation": "valuation/",
            "reconciliation": "reconciliation/",
            "aging": "aging/",
            "slow-moving": "slow-moving/",
            "negative": "negative/",
            "movements": "movements/",
        })

    @action(detail=False, methods=["get"])
    def valuation(self, request):
        report = stock_valuation(as_of=request.query_params.get("as_of"))
        return Response({
            "as_of": report["as_of"],
            "total_value": report["total_value"],
            "rows": [
                {
                    "item": row["item"].sku,
                    "warehouse": row["warehouse"].code,
                    "quantity": row["quantity"],
                    "value": row["value"],
                    "unit_cost": row["unit_cost"],
                    "method": row["method"],
                }
                for row in report["rows"]
            ],
        })

    @action(detail=False, methods=["get"], url_path="reconciliation")
    def reconciliation(self, request):
        """Does the stock agree with the accounts?"""
        report = reconcile_to_ledger(as_of=request.query_params.get("as_of"))
        return Response({
            "as_of": report["as_of"],
            "balanced": report["balanced"],
            "total_stock_value": report["total_stock_value"],
            "total_ledger_balance": report["total_ledger_balance"],
            "difference": report["difference"],
            "rows": [
                {
                    "account": row["account"].code,
                    "stock_value": row["stock_value"],
                    "ledger_balance": row["ledger_balance"],
                    "difference": row["difference"],
                    "items": row["items"],
                }
                for row in report["rows"]
            ],
            "unvalued_items": [item.sku for item in report["unvalued_items"]],
        })

    @action(detail=False, methods=["get"])
    def aging(self, request):
        report = stock_aging(as_of=request.query_params.get("as_of"))
        return Response({
            "as_of": report["as_of"],
            "labels": report["labels"],
            "rows": [
                {
                    "item": row["item"].sku,
                    "warehouse": row["warehouse"].code,
                    "quantity": row["quantity"],
                    "oldest_days": row["oldest_days"],
                    "buckets": row["buckets"],
                }
                for row in report["rows"]
            ],
        })

    @action(detail=False, methods=["get"], url_path="slow-moving")
    def slow(self, request):
        since = request.query_params.get("since")
        if not since:
            raise DRFValidationError("since is required, as a date.")
        report = _run(slow_moving, since)
        return Response({
            "since": report["since"],
            "total_value": report["total_value"],
            "rows": [
                {
                    "item": row["item"].sku,
                    "warehouse": row["warehouse"].code,
                    "quantity": row["quantity"],
                    "value": row["value"],
                    "last_issued": row["last_issued"],
                }
                for row in report["rows"]
            ],
        })

    @action(detail=False, methods=["get"])
    def negative(self, request):
        report = negative_stock(as_of=request.query_params.get("as_of"))
        return Response({
            "rows": [
                {
                    "item": row["item"].sku,
                    "warehouse": row["warehouse"].code,
                    "quantity": row["quantity"],
                    "value": row["value"],
                    "allowed": row["allowed"],
                }
                for row in report["rows"]
            ],
            "unexpected": len(report["unexpected"]),
        })

    @action(detail=False, methods=["get"])
    def movements(self, request):
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        if not (start and end):
            raise DRFValidationError("start and end are required, as dates.")
        report = _run(movement_summary, start, end)
        return Response(report)
