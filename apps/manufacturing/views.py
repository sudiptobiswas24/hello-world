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
from .oee import by_operator, by_shift, effectiveness
from .rolls import FabricRoll
from .tooling import PrintDesign, Tool, ToolUsage, wearing_out
from .routing import Routing, RoutingOperation, capacity_report
from .shifts import Downtime, DowntimeReason, Shift
from .serializers import (
    FabricRollSerializer,
    PrintDesignSerializer,
    ToolSerializer,
    ToolUsageSerializer,
    BagSpecificationSerializer,
    BillOfMaterialsSerializer,
    BomByproductSerializer,
    BomComponentSerializer,
    FabricSpecificationSerializer,
    MaterialIssueLineSerializer,
    MaterialIssueSerializer,
    ProductionByproductSerializer,
    ProductionEntrySerializer,
    DowntimeReasonSerializer,
    DowntimeSerializer,
    RoutingOperationSerializer,
    RoutingSerializer,
    ShiftSerializer,
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


def _window(request):
    """The start and end a report was asked for, or a sentence."""
    start = parse_date(request.query_params.get("start") or "")
    end = parse_date(request.query_params.get("end") or "")
    if start is None or end is None:
        raise DRFValidationError(["start and end are required, as YYYY-MM-DD."])
    return start, end


def _effectiveness_payload(report):
    shift = report["shift"]
    return {
        "work_centre": report["work_centre"].code,
        "shift": getattr(shift, "code", None) if shift else None,
        "shift_name": str(shift) if shift else None,
        "start": report["start"],
        "end": report["end"],
        "availability": report["availability"]["ratio"],
        "ran_minutes": report["availability"]["ran_minutes"],
        "stopped_minutes": report["availability"]["stopped_minutes"],
        "planned_stop_minutes": report["availability"]["planned_stop_minutes"],
        "unplanned_stop_minutes": report["availability"]["unplanned_stop_minutes"],
        "performance": report["performance"]["ratio"],
        "quality": report["quality"]["ratio"],
        "quality_note": report["quality"]["note"],
        "quality_by_item": [
            {"item": row["item"].sku, "good": row["good"],
             "scrapped": row["scrapped"], "ratio": row["ratio"]}
            for row in report["quality"]["by_item"]
        ],
        "oee": report["oee"],
        "unmeasured": report["unmeasured"],
    }


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


class PrintDesignViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PrintDesign.objects.select_related(
        "customer", "approved_by"
    ).prefetch_related("tools")
    serializer_class = PrintDesignSerializer

    @action(detail=True, methods=["get"])
    def cylinders(self, request, pk=None):
        """Whether a full set exists, which decides whether it prints at all."""
        report = self.get_object().cylinder_set()
        return Response({
            "colours": report["colours"],
            "complete": report["complete"],
            "short_by": report["short_by"],
            "tools": ToolSerializer(report["tools"], many=True).data,
        })


class ToolViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Tool.objects.select_related("design", "work_centre", "life_uom")
    serializer_class = ToolSerializer

    @action(detail=False, methods=["get"])
    def wearing_out(self, request):
        """
        Tools past a share of their life, worst first — the report
        that stops a changeover happening mid-run.
        """
        threshold = request.query_params.get("threshold") or "90"
        rows = _run(wearing_out, Decimal(str(threshold)))
        return Response([
            {
                "tool": ToolSerializer(tool).data,
                "used_percent": share,
                "remaining": remaining,
            }
            for tool, share, remaining in rows
        ])


class ToolUsageViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only: wear is derived from bookings, never typed in."""

    queryset = ToolUsage.objects.select_related("tool", "entry")
    serializer_class = ToolUsageSerializer


class FabricRollViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    Rolls, and the metres on each of them.

    `metres/` is the question the cutting table asks and the one the
    unit-of-measure graph cannot answer, because every roll converts
    at its own weight per metre.
    """

    queryset = FabricRoll.objects.select_related(
        "lot", "lot__item", "specification", "entry"
    )
    serializer_class = FabricRollSerializer

    @action(detail=False, methods=["get"])
    def metres(self, request):
        from apps.inventory.models import Item, Warehouse

        from .rolls import metres_on_hand, rolls_at, unrolled_stock

        item = Item.objects.filter(pk=request.query_params.get("item")).first()
        warehouse = Warehouse.objects.filter(
            pk=request.query_params.get("warehouse")
        ).first()
        if item is None or warehouse is None:
            raise DRFValidationError(["Name an item and a warehouse."])
        return Response({
            "item": item.pk,
            "warehouse": warehouse.pk,
            "metres": metres_on_hand(item, warehouse),
            # Reported rather than ignored: fabric with no roll behind
            # it converts to no metres, so a cutting table reading the
            # total alone is being told about less than the plant owns.
            "kilos_with_no_roll": unrolled_stock(item, warehouse),
            "rolls": [
                {
                    "roll": roll.pk, "lot": roll.lot.code,
                    "kilos": quantity, "metres": metres,
                }
                for roll, quantity, metres in rolls_at(item, warehouse)
            ],
        })


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
    def effectiveness(self, request, pk=None):
        """
        Availability, performance and quality, and their product where
        all three exist. A ratio nobody measured comes back missing
        rather than as one.
        """
        centre = self.get_object()
        start, end = _window(request)
        shift = None
        code = request.query_params.get("shift")
        if code:
            shift = Shift.objects.filter(code=code, is_active=True).first()
            if shift is None:
                raise DRFValidationError([f"No active shift with code {code}."])
        return Response(_effectiveness_payload(
            _run(effectiveness, centre, start, end, shift=shift)
        ))

    @action(detail=True, methods=["get"], url_path="by-shift")
    def by_shift(self, request, pk=None):
        """A row per crew's slot, plus the hours nobody attributed."""
        centre = self.get_object()
        start, end = _window(request)
        return Response([
            _effectiveness_payload(row)
            for row in _run(by_shift, centre, start, end)
        ])

    @action(detail=True, methods=["get"])
    def capacity(self, request, pk=None):
        """
        What this machine is being asked to do in a window against what
        it can, and what is queued with no date on it at all.
        """
        centre = self.get_object()
        start, end = _window(request)
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


class ShiftViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Shift.objects.all()
    serializer_class = ShiftSerializer


class DowntimeReasonViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = DowntimeReason.objects.all()
    serializer_class = DowntimeReasonSerializer


class DowntimeViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Downtime.objects.select_related(
        "work_centre", "shift", "reason", "work_order"
    )
    serializer_class = DowntimeSerializer


class OperatorYieldViewSet(viewsets.ViewSet):
    """
    What each person's hours produced.

    Attribution, not appraisal — a crew on the oldest loom in the shed
    reads worse than one on the newest, so every row names the machine.
    """

    # Not a list of these rows — they are computed. It is here so the
    # permission check has a model to ask about, and time bookings are
    # the right one: reading who produced what is reading their hours.
    queryset = TimeBooking.objects.none()

    def list(self, request):
        start, end = _window(request)
        centre = None
        code = request.query_params.get("work_centre")
        if code:
            centre = WorkCentre.objects.filter(code=code).first()
            if centre is None:
                raise DRFValidationError([f"No work centre with code {code}."])
        return Response([
            {
                "operator": row["operator"].employee_number,
                "name": row["operator"].party.name,
                "work_centre": row["work_centre"].code,
                "minutes": row["minutes"],
                "ideal_minutes": row["ideal_minutes"],
                "performance": row["performance"],
                "bookings": row["bookings"],
            }
            for row in _run(by_operator, start, end, work_centre=centre)
        ])
