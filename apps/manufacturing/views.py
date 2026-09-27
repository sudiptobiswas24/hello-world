from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin
from apps.core.permissions import ActionPermission
from apps.inventory.models import Lot, Warehouse

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
from .trace import recall
from .changeover import ChangeoverRule, SetupFamily
from .changeover import sequence as changeover_sequence
from .jobwork import JobWorkChallan, JobWorkLine, JobWorkLoss
from .machines import Machine
from .oee import by_machine as oee_by_machine
from .oee import by_operator, by_shift, effectiveness
from .bom import BomSubstitute
from .costing import CostVersion, StandardCost, against_actual, explain
from .maintenance import MaintenanceJob, MaintenanceSchedule, due_now
from .rolls import FabricRoll
from .tooling import PrintDesign, Tool, ToolUsage, wearing_out
from .routing import Routing, RoutingOperation, capacity_report
from .shifts import Downtime, DowntimeReason, Shift
from .serializers import (
    ChangeoverRuleSerializer,
    MachineSerializer,
    SetupFamilySerializer,
    BomSubstituteSerializer,
    CostVersionSerializer,
    StandardCostSerializer,
    MaintenanceJobSerializer,
    MaintenanceScheduleSerializer,
    FabricRollSerializer,
    PrintDesignSerializer,
    ToolSerializer,
    ToolUsageSerializer,
    BagSolveSerializer,
    BagSpecificationSerializer,
    BillOfMaterialsSerializer,
    BomByproductSerializer,
    BomComponentSerializer,
    FabricSpecificationSerializer,
    JobWorkChallanSerializer,
    JobWorkLineSerializer,
    JobWorkLossSerializer,
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
from .woven import BagSpecification, FabricSpecification, TapeSpecification, denier_for


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
    machine = report.get("machine")
    if report["availability"] is None:
        # The row for hours nobody put a machine on. It has no
        # ratios, and inventing zeroes for it would read as a loom
        # that stood still rather than as bookings nobody attributed.
        return {
            "work_centre": report["work_centre"].code,
            "machine": str(machine) if machine else None,
            "start": report["start"],
            "end": report["end"],
            "availability": None,
            "performance": None,
            "quality": None,
            "oee": None,
            "unmeasured": report["unmeasured"],
            "loose_minutes": report.get("loose_minutes"),
            "loose_bookings": report.get("loose_bookings"),
            "note": report.get("note"),
        }
    return {
        "work_centre": report["work_centre"].code,
        "machine": getattr(machine, "code", None) if machine else None,
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
    # Solving writes nothing, so it asks only to see specifications:
    # whoever quotes a sack need not be allowed to create one.
    action_permission_map = {"solve": "manufacturing.view_bagspecification"}

    @action(detail=False, methods=["post"],
            permission_classes=[IsAuthenticated, ActionPermission])
    def solve(self, request):
        """
        From a contracted weight to the fabric that makes it: the GSM,
        the deniers in the fabric and on the tape line, and every fabric
        already specified at that width that would land the sack inside
        its tolerance, nearest first.
        """
        serializer = BagSolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        target = data.pop("target_grams")
        mesh = (data.pop("ends_per_inch"), data.pop("picks_per_inch"))
        shrink = data.pop("shrink_percent")
        warp = data.pop("warp_tape_denier", None)
        sack = BagSpecification(**data)
        try:
            sack._check_shape()
            gsm = sack.fabric_gsm_for(target)
            deniers = denier_for(gsm, *mesh, shrink, warp)
        except DjangoValidationError as error:
            raise DRFValidationError(error.messages)
        area, addons = sack.fabric_area_sqm(), sack.addon_grams()
        tolerance = sack.weight_tolerance_percent
        matches = []
        # A fabric whose specification has already ended cannot be made
        # to; one staged for later can, and a quote is usually for later.
        current = Q(valid_to__isnull=True) | Q(valid_to__gte=timezone.localdate())
        for fabric in FabricSpecification.objects.filter(
            current, is_active=True, weave="tubular", lay_flat_width_cm=sack.bag_width_cm,
        ).select_related("warp_tape", "weft_tape"):
            weight = addons + area * fabric.gsm()
            deviation = (weight - target) / target * 100
            if abs(deviation) <= tolerance:
                matches.append((abs(deviation), fabric.code, {
                    "fabric": fabric.pk, "code": fabric.code,
                    "gsm": str(round(fabric.gsm(), 3)),
                    "bag_grams": str(round(weight, 3)),
                    # round() keeps the sign of a deviation too small to
                    # show, and "-0.00" reads as under weight when it is not.
                    "deviation_percent": str(round(deviation, 2) + 0),
                }))
        return Response({
            "target_grams": str(target),
            "addon_grams": str(round(addons, 3)),
            "fabric_area_sqm": str(round(area, 6)),
            "fabric_gsm": str(round(gsm, 3)),
            **{key: str(round(value, 1)) for key, value in deniers.items()},
            "shrink_percent": str(shrink),
            "fabrics": [row for _, _, row in sorted(matches, key=lambda m: m[:2])],
        })


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


class BomSubstituteViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BomSubstitute.objects.select_related(
        "item", "component", "component__item", "component__bom"
    )
    serializer_class = BomSubstituteSerializer


class CostVersionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    Standard costs, rolled and published.

    `publish/` is a POST and it posts a journal entry, because a
    standard cost change is a revaluation of every standard-costed
    shelf in the company.
    """

    queryset = CostVersion.objects.prefetch_related("costs")
    serializer_class = CostVersionSerializer
    action_permission_map = {
        "roll_up": "manufacturing.change_costversion",
        "publish": "manufacturing.change_costversion",
    }

    @action(detail=True, methods=["post"])
    def roll_up(self, request, pk=None):
        version = self.get_object()
        done = _run(version.roll_up)
        return Response({"version": version.pk, "rolled": len(done)})

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        version = self.get_object()
        entry = _run(
            version.publish,
            on_date=request.data.get("on_date"),
            by=request.user if request.user.is_authenticated else None,
        )
        return Response({
            **self.get_serializer(version).data,
            "revaluation": entry.pk if entry else None,
        })

    @action(detail=True, methods=["get"])
    def explain(self, request, pk=None):
        """Why an item costs what it costs, one level down."""
        from apps.inventory.models import Item

        item = Item.objects.filter(pk=request.query_params.get("item")).first()
        if item is None:
            raise DRFValidationError(["Name an item."])
        report = _run(explain, self.get_object(), item)
        return Response({
            "item": report["item"].pk,
            "sku": report["item"].sku,
            "cost": report["cost"],
            "bought": report["bought"],
            "material": report.get("material"),
            "conversion": report.get("conversion"),
            "byproduct_credit": report.get("byproduct_credit"),
            "lines": [
                {
                    "item": line["item"].pk, "sku": line["item"].sku,
                    "quantity_per_unit": line["quantity_per_unit"],
                    "rate": line["rate"],
                    "cost_per_unit": line["cost_per_unit"],
                    "share_percent": line["share_percent"],
                }
                for line in report["lines"]
            ],
        })

    @action(detail=True, methods=["get"])
    def against_actual(self, request, pk=None):
        """Where the standard has drifted from the shelf, worst first."""
        return Response([
            {
                "item": row["item"].pk, "sku": row["item"].sku,
                "standard": row["standard"], "actual": row["actual"],
                "difference": row["difference"],
                "difference_percent": row["difference_percent"],
            }
            for row in _run(against_actual, self.get_object())
        ])


class StandardCostViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StandardCost.objects.select_related("version", "item", "bom")
    serializer_class = StandardCostSerializer


class MaintenanceScheduleViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = MaintenanceSchedule.objects.select_related("work_centre")
    serializer_class = MaintenanceScheduleSerializer
    action_permission_map = {"raise_job": "manufacturing.add_maintenancejob"}

    @action(detail=False, methods=["get"])
    def due(self, request):
        """What has run out on either clock and has no job on the board."""
        return Response(
            MaintenanceScheduleSerializer(_run(due_now), many=True).data
        )

    @action(detail=True, methods=["post"])
    def raise_job(self, request, pk=None):
        job = _run(self.get_object().raise_job, request.data.get("due_on"))
        return Response(MaintenanceJobSerializer(job).data)


class MaintenanceJobViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = MaintenanceJob.objects.select_related(
        "schedule", "work_centre", "downtime"
    )
    serializer_class = MaintenanceJobSerializer
    action_permission_map = {"complete": "manufacturing.change_maintenancejob"}

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        job = self.get_object()
        _run(
            job.complete,
            on_date=request.data.get("on_date"),
            minutes=request.data.get("minutes"),
        )
        return Response(self.get_serializer(job).data)


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


class SetupFamilyViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Which family an item belongs to on a machine."""

    queryset = SetupFamily.objects.select_related("item", "work_centre").all()
    serializer_class = SetupFamilySerializer


class ChangeoverRuleViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Minutes to change a machine from one family to another."""

    queryset = ChangeoverRule.objects.select_related("work_centre").all()
    serializer_class = ChangeoverRuleSerializer


class MachineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Named machines: which loom, which extruder, which press."""

    queryset = Machine.objects.select_related("work_centre").all()
    serializer_class = MachineSerializer


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
    def sequence(self, request, pk=None):
        """
        The queue on this machine as planned, and the order that changes
        over least — a proposal, with the runs it would make later named.
        Pass `machine` (a machine code) to ask about one loom or press.
        """
        centre = self.get_object()
        machine = None
        code = request.query_params.get("machine")
        if code:
            machine = Machine.objects.filter(code=code).first()
            if machine is None:
                raise DRFValidationError([f"No machine with code {code}."])
        result = _run(changeover_sequence, centre, machine)

        def rows(sequence):
            return [
                {
                    "work_order": row["operation"].work_order.number,
                    "item": row["item"].sku,
                    "family": row["family"],
                    "changeover_minutes": row["changeover_minutes"],
                }
                for row in sequence
            ]

        current = result["on_the_machine"]
        return Response({
            "work_centre": centre.code,
            "machine": machine.code if machine else None,
            "on_the_machine": current.sku if current else None,
            "planned": rows(result["planned"]),
            "planned_minutes": result["planned_minutes"],
            "proposed": rows(result["proposed"]),
            "proposed_minutes": result["proposed_minutes"],
            "saved_minutes": result["saved_minutes"],
            "moved_later": [
                op.work_order.number for op in result["moved_later"]
            ],
            "note": result["note"],
        })

    @action(detail=True, methods=["get"], url_path="by-machine")
    def by_machine(self, request, pk=None):
        """
        A row per machine in the bank, which is the grouping the
        number exists for. A shed at 78% is two dead looms and thirty
        good ones, or thirty-two mediocre ones.

        These rows do not add up to the bank's own figures: a
        bank-wide stoppage counts once against every machine it
        stopped.
        """
        centre = self.get_object()
        start, end = _window(request)
        return Response([
            _effectiveness_payload(row)
            for row in _run(oee_by_machine, centre, start, end)
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

    @action(detail=True, methods=["get"], url_path="variance")
    def money_variance(self, request, pk=None):
        """
        What is left in the run and why: machine time, vendors' work and
        material, and how much of the material its own measurements
        explain — by inspection and by the roll scale, which weighs
        every roll whether or not anything reached a laboratory.
        """
        from .explain import explains

        order = self.get_object()
        explained = {}
        for source in ("gsm", "weighed"):
            report = explains(order, source)
            explained[source] = None if report is None else {
                key: report[key] for key in (
                    "target", "measured", "deviation_percent", "accounted_for",
                    "share_percent", "note",
                )
            }
        return Response({
            "unaccounted": order.unaccounted(),
            "machine_time": order.conversion_variance(),
            "machine_minutes": order.time_variance_minutes(),
            "vendors": order.outside_variance(),
            "material": order.material_overrun(),
            "explained": explained,
        })

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


def _lot_row(lot):
    return {"id": lot.pk, "code": lot.code, "item": lot.item.sku}


class LotTraceViewSet(viewsets.ViewSet):
    """
    A batch traced both ways: back to what it was made from, for a
    complaint; forward to what was made from it and who holds it, for a
    recall.
    """

    # Computed, not listed: here so the permission check has a model to
    # ask about, and reading a trace is reading lots.
    queryset = Lot.objects.none()

    def _lot(self, pk):
        lot = Lot.objects.select_related("item").filter(pk=pk).first()
        if lot is None:
            raise DRFValidationError([f"No lot {pk}."])
        return lot

    def _depth(self, request):
        try:
            depth = int(request.query_params.get("depth", 4))
        except ValueError:
            raise DRFValidationError(["depth must be a whole number."])
        if not 1 <= depth <= 10:
            raise DRFValidationError(["depth must be between 1 and 10."])
        return depth

    @action(detail=True, methods=["get"], url_path="made-from")
    def made_from(self, request, pk=None):
        lot = self._lot(pk)
        return Response([
            {
                "level": row["level"], "lot": _lot_row(row["lot"]),
                "made_by": row["made_by"].number,
                "from_lot": _lot_row(row["from_lot"]), "quantity": row["quantity"],
            }
            for row in genealogy(lot, depth=self._depth(request))
        ])

    @action(detail=True, methods=["get"])
    def recall(self, request, pk=None):
        lot = self._lot(pk)
        report = recall(lot, depth=self._depth(request))
        return Response({
            "lot": _lot_row(lot),
            "descendants": [
                {
                    "level": row["level"], "lot": _lot_row(row["lot"]),
                    "used_by": row["used_by"].number, "quantity": row["quantity"],
                    "made": [_lot_row(child) for child in row["made"]],
                }
                for row in report["descendants"]
            ],
            "customers": [
                {
                    "customer": row["customer"].code, "name": row["customer"].name,
                    "lot": _lot_row(row["lot"]), "quantity": row["quantity"],
                    "deliveries": row["deliveries"],
                }
                for row in report["customers"]
            ],
            "not_followed": [_lot_row(lot) for lot in report["not_followed"]],
        })



class JobWorkChallanViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    Challans for goods going out to a job worker. Drafted and edited
    freely; issued with `post`, withdrawn with `void`, never edited once
    issued.
    """

    queryset = JobWorkChallan.objects.select_related("job_worker").prefetch_related("lines")
    serializer_class = JobWorkChallanSerializer
    action_permission_map = {
        "post": "manufacturing.change_jobworkchallan",
        "void": "manufacturing.change_jobworkchallan",
    }

    def perform_create(self, serializer):
        _run(serializer.save)

    def perform_update(self, serializer):
        _run(serializer.save)

    def perform_destroy(self, instance):
        if instance.posted:
            raise DRFValidationError([f"{instance} is issued. Void it instead."])
        instance.delete()

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        challan = self.get_object()
        _run(challan.post)
        return Response(self.get_serializer(challan).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        challan = self.get_object()
        _run(challan.void)
        return Response(self.get_serializer(challan).data)

    @action(detail=False, methods=["get"], url_path="still-out")
    def still_out(self, request):
        """What is out, when it must be back, and whether that day has passed."""
        from .jobwork import still_out

        given = request.query_params.get("as_of")
        try:
            as_of = parse_date(given) if given else None
        except ValueError:
            as_of = None
        # parse_date answers None for a string that is no date at all;
        # read as "today", a typo would quietly report the wrong day.
        if given and as_of is None:
            raise DRFValidationError(["as_of must be a date."])
        rows = still_out(as_of=as_of)
        return Response([
            {
                "challan": row["challan"].number, "challan_date": row["challan"].challan_date,
                "job_worker": row["challan"].job_worker.name,
                "description": row["line"].description,
                "outstanding": str(row["outstanding"]),
                "due_back_by": row["due_back_by"], "overdue": row["overdue"],
                "due_soon": row["due_soon"],
            }
            for row in rows
        ])


class JobWorkLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = JobWorkLine.objects.select_related("challan", "operation")
    serializer_class = JobWorkLineSerializer

    def perform_create(self, serializer):
        _run(serializer.save)

    def perform_update(self, serializer):
        _run(serializer.save)

    def perform_destroy(self, instance):
        if instance.challan.posted:
            raise DRFValidationError([f"{instance.challan} is issued; its lines are fixed."])
        instance.delete()


class JobWorkLossViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Losses at a job worker: recorded, never edited or deleted."""

    queryset = JobWorkLoss.objects.select_related("line")
    serializer_class = JobWorkLossSerializer
    http_method_names = ["get", "post", "head", "options"]

    def perform_create(self, serializer):
        _run(serializer.save)



class DispatchViewSet(viewsets.ViewSet):
    """
    The detailed schedule by machine. Reading it changes nothing;
    committing it writes each operation's machine and planned times.
    """

    permission_classes = [IsAuthenticated, ActionPermission]
    action_permission_map = {"commit": "manufacturing.change_workorderoperation"}

    @staticmethod
    def _row(row):
        order = row["order"]
        return {
            "run": order.number, "item": order.item.sku,
            "operation": row["operation"].name, "sequence": row["operation"].sequence,
            "start": row["start"], "finish": row["finish"],
            "changeover_minutes": str(row["changeover"]), "late": row["late"],
            "due": order.scheduled_end,
        }

    def list(self, request):
        from .dispatch import build, dispatch_list

        board = dispatch_list(build())
        return Response({code: [self._row(row) for row in rows] for code, rows in board.items()})

    @action(detail=False, methods=["post"])
    def commit(self, request):
        from .dispatch import build, commit

        return Response({"committed": _run(commit, build())})
