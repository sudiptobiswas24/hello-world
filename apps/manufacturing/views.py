from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin
from apps.inventory.models import Warehouse

from .bom import (
    BillOfMaterials,
    BomByproduct,
    BomComponent,
    explode,
    material_balance,
    net_requirements,
)
from .orders import (
    MaterialIssue,
    MaterialIssueLine,
    ProductionByproduct,
    ProductionEntry,
    TimeBooking,
    WorkCentre,
    WorkOrder,
)
from .demand import coverage, genealogy, uncovered
from .routing import Routing, RoutingOperation, capacity_report
from .serializers import (
    BagSpecificationSerializer,
    BillOfMaterialsSerializer,
    BomByproductSerializer,
    BomComponentSerializer,
    FabricSpecificationSerializer,
    MaterialIssueLineSerializer,
    MaterialIssueSerializer,
    ProductionByproductSerializer,
    ProductionEntrySerializer,
    RoutingOperationSerializer,
    RoutingSerializer,
    TapeSpecificationSerializer,
    TimeBookingSerializer,
    WorkCentreSerializer,
    WorkOrderSerializer,
)
from .woven import BagSpecification, FabricSpecification, TapeSpecification


def _run(callable_, *args, **kwargs):
    """Let a model's refusal reach the caller as the sentence it wrote."""
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


def _quantity(request, default="1"):
    try:
        return Decimal(str(request.query_params.get("quantity") or default))
    except InvalidOperation:
        raise DRFValidationError(["quantity must be a number."])


class TapeSpecificationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TapeSpecification.objects.select_related("tape_item", "bom")
    serializer_class = TapeSpecificationSerializer


class FabricSpecificationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = FabricSpecification.objects.select_related(
        "fabric_item", "warp_tape", "weft_tape", "bom"
    )
    serializer_class = FabricSpecificationSerializer


class BagSpecificationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BagSpecification.objects.select_related("bag_item", "fabric", "bom")
    serializer_class = BagSpecificationSerializer


class BillOfMaterialsViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BillOfMaterials.objects.prefetch_related("components", "byproducts")
    serializer_class = BillOfMaterialsSerializer

    @action(detail=True, methods=["get"])
    def explosion(self, request, pk=None):
        """Every level of it, in the order the plant works in."""
        bom = self.get_object()
        rows = _run(explode, bom, _quantity(request), bom.uom)
        return Response([
            {
                "level": row.level,
                "item": row.item.sku,
                "name": row.item.name,
                "quantity": row.quantity,
                "uom": str(row.uom),
                "is_byproduct": row.is_byproduct,
                "is_leaf": row.is_leaf,
                "from_bom": str(row.bom),
            }
            for row in rows
        ])

    @action(detail=True, methods=["get"], url_path="requirements")
    def requirements(self, request, pk=None):
        """
        What has to be found on a shelf, against what is on it.

        Only the leaves: an item this plant makes is a reason to raise
        another work order, not something to go looking for.
        """
        bom = self.get_object()
        quantity = _quantity(request)
        warehouse = None
        code = request.query_params.get("warehouse")
        if code:
            warehouse = Warehouse.objects.filter(code=code).first()
            if warehouse is None:
                raise DRFValidationError([f"No warehouse with code {code}."])
        rows = []
        for item, needed, uom in _run(net_requirements, bom, quantity, bom.uom):
            on_hand = item.on_hand_at(warehouse)
            rows.append({
                "item": item.sku,
                "name": item.name,
                "required": needed,
                "uom": str(uom),
                "on_hand": on_hand,
                "short_by": max(
                    item.to_stock_quantity(needed, uom) - on_hand, Decimal("0")
                ),
            })
        return Response(rows)

    @action(detail=True, methods=["get"], url_path="material-balance")
    def balance(self, request, pk=None):
        """
        The requirement and the recovery in the same row.

        A blend calling for fifteen per cent reprocessed material
        against processes recovering six is a plant that buys scrap from
        somebody, and no conventional bill of materials says so.
        """
        bom = self.get_object()
        rows = _run(material_balance, bom, _quantity(request), bom.uom)
        return Response([
            {
                "item": item.sku,
                "name": item.name,
                "required": required,
                "produced": produced,
                "net": net,
            }
            for item, required, produced, net in rows
        ])


class BomComponentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BomComponent.objects.select_related("item", "bom")
    serializer_class = BomComponentSerializer


class BomByproductViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BomByproduct.objects.select_related("item", "bom")
    serializer_class = BomByproductSerializer


class RoutingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Routing.objects.prefetch_related("operations")
    serializer_class = RoutingSerializer


class RoutingOperationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = RoutingOperation.objects.select_related("routing", "work_centre")
    serializer_class = RoutingOperationSerializer


class WorkCentreViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = WorkCentre.objects.all()
    serializer_class = WorkCentreSerializer

    @action(detail=True, methods=["get"])
    def capacity(self, request, pk=None):
        """
        What this machine is being asked to do in a window against what
        it can, and what is queued with no date on it at all.
        """
        centre = self.get_object()
        start = parse_date(request.query_params.get("start") or "")
        end = parse_date(request.query_params.get("end") or "")
        if start is None or end is None:
            raise DRFValidationError(
                ["start and end are required, as YYYY-MM-DD."]
            )
        report = _run(capacity_report, centre, start, end)
        return Response({
            "work_centre": centre.code,
            "start": report["start"],
            "end": report["end"],
            "load_minutes": report["load_minutes"],
            "unscheduled_minutes": report["unscheduled_minutes"],
            "available_minutes": report["available_minutes"],
            "spare_minutes": report["spare_minutes"],
            "utilisation_percent": report["utilisation_percent"],
            "runs": [order.number or f"draft {order.pk}" for order in report["runs"]],
        })


class WorkOrderViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = WorkOrder.objects.select_related(
        "item", "bom", "warehouse", "work_centre"
    ).prefetch_related("components")
    serializer_class = WorkOrderSerializer
    action_permission_map = {
        "release": "manufacturing.change_workorder",
        "close": "manufacturing.change_workorder",
        "reopen": "manufacturing.change_workorder",
        "cancel": "manufacturing.change_workorder",
    }

    def _reply(self, order):
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        order = self.get_object()
        _run(order.release, on_date=request.data.get("on_date"))
        return self._reply(order)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        order = self.get_object()
        _run(
            order.close,
            on_date=request.data.get("on_date"), memo=request.data.get("memo", ""),
        )
        return self._reply(order)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        order = self.get_object()
        _run(
            order.reopen,
            on_date=request.data.get("on_date"), memo=request.data.get("memo", ""),
        )
        return self._reply(order)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        _run(order.cancel)
        return self._reply(order)

    @action(detail=True, methods=["get"], url_path="coverage")
    def coverage(self, request, pk=None):
        """What the customer line this run is for still has uncovered."""
        order = self.get_object()
        if order.sales_order_line_id is None:
            raise DRFValidationError(["This run is not against a customer line."])
        report = coverage(order.sales_order_line)
        return Response({
            "sales_order": str(report["line"].order),
            "item": report["item"].sku,
            "ordered": report["ordered"],
            "on_work_orders": report["on_work_orders"],
            "made": report["made"],
            "shipped": report["shipped"],
            "uncovered": report["uncovered"],
            "runs": [run.number or f"draft {run.pk}" for run in report["runs"]],
        })

    @action(detail=False, methods=["get"], url_path="uncovered")
    def uncovered(self, request):
        """
        The planner's morning list: every customer line with something
        nobody has started making.
        """
        return Response([
            {
                "sales_order": str(row["line"].order),
                "line": row["line"].pk,
                "item": row["item"].sku,
                "name": row["item"].name,
                "ordered": row["ordered"],
                "on_work_orders": row["on_work_orders"],
                "uncovered": row["uncovered"],
            }
            for row in uncovered()
        ])

    @action(detail=True, methods=["get"], url_path="material-variance")
    def variance(self, request, pk=None):
        """Kilo for kilo, what the run took against what it should have."""
        order = self.get_object()
        return Response([
            {
                "item": item.sku,
                "name": item.name,
                "expected": expected,
                "actual": actual,
                "difference": difference,
            }
            for item, expected, actual, difference in order.material_variance()
        ])


class MaterialIssueViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = MaterialIssue.objects.select_related(
        "work_order", "warehouse"
    ).prefetch_related("lines")
    serializer_class = MaterialIssueSerializer
    action_permission_map = {
        "post": "manufacturing.change_materialissue",
        "void": "manufacturing.change_materialissue",
    }

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        issue = self.get_object()
        _run(issue.post, memo=request.data.get("memo", ""))
        return Response(self.get_serializer(issue).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        issue = self.get_object()
        _run(
            issue.void,
            on_date=request.data.get("on_date"), memo=request.data.get("memo", ""),
        )
        return Response(self.get_serializer(issue).data)


class MaterialIssueLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = MaterialIssueLine.objects.select_related("item", "issue")
    serializer_class = MaterialIssueLineSerializer


class ProductionEntryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ProductionEntry.objects.select_related(
        "work_order", "warehouse", "work_centre"
    ).prefetch_related("byproducts")
    serializer_class = ProductionEntrySerializer
    action_permission_map = {
        "post": "manufacturing.change_productionentry",
        "void": "manufacturing.change_productionentry",
    }

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        entry = self.get_object()
        _run(entry.post, memo=request.data.get("memo", ""))
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        entry = self.get_object()
        _run(
            entry.void,
            on_date=request.data.get("on_date"), memo=request.data.get("memo", ""),
        )
        return Response(self.get_serializer(entry).data)


class ProductionByproductViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ProductionByproduct.objects.select_related("item", "entry")
    serializer_class = ProductionByproductSerializer


class TimeBookingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TimeBooking.objects.select_related(
        "work_order", "operation", "operation__work_centre"
    )
    serializer_class = TimeBookingSerializer
    action_permission_map = {
        "post": "manufacturing.change_timebooking",
        "void": "manufacturing.change_timebooking",
    }

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        booking = self.get_object()
        _run(booking.post, memo=request.data.get("memo", ""))
        return Response(self.get_serializer(booking).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        booking = self.get_object()
        _run(
            booking.void,
            on_date=request.data.get("on_date"), memo=request.data.get("memo", ""),
        )
        return Response(self.get_serializer(booking).data)
