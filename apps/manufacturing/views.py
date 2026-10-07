import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.api import plain, record_or_404
from apps.core.audit import AuditableViewSetMixin
from apps.core.models import to_date
from apps.core.permissions import ActionPermission, RequiredPermission
from apps.inventory.models import Item, Lot, StockAdjustmentLine, Warehouse
from apps.sales.models import SalesOrderLine

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
    WorkOrderComponent,
    WorkOrderOperation,
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
from . import certificates, quoting
from .certificates import TestCertificate
from . import energy
from .energy import EnergyMeter, EnergyTariff, MeterReading
from .bales import Bale, BaleLine
from . import inward
from .inward import (
    CustomerMaterialReceipt,
    CustomerMaterialReceiptLine,
    CustomerMaterialReturn,
    CustomerMaterialReturnLine,
)
from .quoting import CostSheet, MaterialRate, QuotePolicy, StageRate
from .rolls import FabricRoll
from .tooling import PrintDesign, Tool, ToolUsage, wearing_out
from .manning import CrewAssignment
from .routing import AlternateRouting, Routing, RoutingOperation, capacity_report
from .shifts import Downtime, DowntimeReason, Shift
from . import scrap
from .scrap import OperationReport, ProductionScrap, ScrapReason
from . import rebatch as rebatching
from .rebatch import Rebatch
from .serializers import (
    CoatingLineSerializer,
    RebatchSerializer,
    OperationReportSerializer,
    ProductionScrapSerializer,
    ScrapReasonSerializer,
    ChangeoverRuleSerializer,
    MachineSerializer,
    SetupFamilySerializer,
    BomSubstituteSerializer,
    CostVersionSerializer,
    StandardCostSerializer,
    MaintenanceJobDetailSerializer,
    MaintenanceJobSerializer,
    MaintenanceScheduleSerializer,
    FabricRollSerializer,
    PrintDesignSerializer,
    ToolSerializer,
    ToolUsageSerializer,
    BagSolveSerializer,
    BagSpecificationSerializer,
    CostSheetSerializer,
    CostSheetRequestSerializer,
    MaterialRateSerializer,
    QuotePolicySerializer,
    QuoteRequestSerializer,
    StageRateSerializer,
    EnergyMeterSerializer,
    CustomerMaterialReceiptLineSerializer,
    CustomerMaterialReceiptSerializer,
    CustomerMaterialReturnLineSerializer,
    CustomerMaterialReturnSerializer,
    EnergyTariffSerializer,
    MeterReadingSerializer,
    TestCertificateSerializer,
    BillOfMaterialsSerializer,
    BomByproductSerializer,
    BomComponentSerializer,
    FabricSpecificationSerializer,
    JobWorkChallanSerializer,
    JobWorkLineSerializer,
    JobWorkLossSerializer,
    MaterialIssueLineSerializer,
    MaterialIssueSerializer,
    OperationChoiceSerializer,
    ProductionByproductSerializer,
    ProductionEntrySerializer,
    DowntimeReasonSerializer,
    DowntimeSerializer,
    RoutingOperationSerializer,
    RoutingSerializer,
    ShiftSerializer,
    TapeSpecificationSerializer,
    FilmSpecificationSerializer,
    AlternateRoutingSerializer,
    CrewAssignmentSerializer,
    LinerSpecificationSerializer,
    TimeBookingSerializer,
    WorkCentreSerializer,
    WorkOrderListSerializer,
    WorkOrderSerializer,
)
from .liners import FilmSpecification, LinerSpecification
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


class OrderProfitabilityViewSet(viewsets.ViewSet):
    """?order= : each line's quote, actual cost, revenue and margin."""

    # Computed, not listed: here so the permission check has a model to ask about.
    queryset = SalesOrderLine.objects.none()

    def list(self, request):
        from .profitability import line_profitability

        if not request.user.has_perm("manufacturing.view_costsheet"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("An order's profitability is its costs: it is read by whoever reads "
                                   "cost sheets.")
        order = request.query_params.get("order")
        if not order:
            raise DRFValidationError(["Name the sales order: ?order=<id>."])

        def text(value, places="0.0001"):
            return None if value is None else str(Decimal(value).quantize(Decimal(places)))

        def figures(row):
            return None if row is None else {key: text(row[key]) for key in (
                "material", "conversion", "credit", "direct")}

        from apps.sales.scoping import UNLIMITED, carried_by, rep_limit

        lines = SalesOrderLine.objects.filter(order_id=order, item__isnull=False)
        rep = rep_limit(request.user)
        if rep is not UNLIMITED:
            lines = lines.filter(carried_by(rep, "order__customer"))
        rows = []
        for line in lines:
            found = line_profitability(line)
            quote = found["quoted"]
            rows.append({
                "line": line.pk, "item": line.item.sku,
                "quoted": None if quote is None else {
                    **figures(quote), "overhead": text(quote["overhead"]),
                    "cost": text(quote["cost"]), "price": text(quote["price"], "0.01")},
                "actual": figures(found["actual"]), "runs": found["runs"],
                "final": found["final"], "made": text(found["made"]),
                "shipped": text(found["shipped"]), "revenue": text(found["revenue"], "0.01"),
                "realised_price": text(found["realised_price"]),
                "cost_of_shipped": text(found["cost_of_shipped"], "0.01"),
                "margin": text(found["margin"], "0.01"),
                "margin_percent": text(found["margin_percent"], "0.01"),
            })
        return Response(rows)


class CrewAssignmentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Who is on which bank's shift; ?work_centre= to see one bank's."""
    extra_params = ('work_centre',)

    queryset = CrewAssignment.objects.select_related("employee__party", "work_centre",
                                                     "shift")
    serializer_class = CrewAssignmentSerializer
    filter_fields = ["shift", "employee"]

    def get_queryset(self):
        rows = super().get_queryset()
        centre = self.request.query_params.get("work_centre")
        return rows.filter(work_centre_id=centre) if centre else rows


class AlternateRoutingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Other ways a bill is made, tried in priority after its own routing."""
    extra_params = ('bom',)

    queryset = AlternateRouting.objects.select_related("bom", "routing")
    serializer_class = AlternateRoutingSerializer

    def get_queryset(self):
        rows = super().get_queryset()
        bom = self.request.query_params.get("bom")
        return rows.filter(bom_id=bom) if bom else rows


class FilmSpecificationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Blown liner film: its blend, thickness and width; its bill is built from them."""

    queryset = FilmSpecification.objects.select_related("film_item", "bom")
    serializer_class = FilmSpecificationSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]


class LinerSpecificationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """A liner cut from a film; a lined sack carries one and weighs what this says."""

    queryset = LinerSpecification.objects.select_related("liner_item", "film", "bom")
    serializer_class = LinerSpecificationSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]


class TapeSpecificationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TapeSpecification.objects.select_related("tape_item", "bom")
    serializer_class = TapeSpecificationSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]

    @action(detail=True, methods=["get"])
    def strength(self, request, pk=None):
        """What the plant's own batches say this tape will measure; ?filler= to ask
        about another filler before changing the recipe."""
        from . import strength

        spec = self.get_object()
        filler = request.query_params.get("filler")
        try:
            filler = Decimal(filler) if filler not in (None, "") else None
        except InvalidOperation:
            raise DRFValidationError(["filler is a percentage."])
        rows = _run(strength.check, spec, filler)

        def text(value):
            # format(), not str(): a filler asked as 1E+1 reads back as 10.
            return None if value is None else format(value, "f")

        return Response([{
            **{key: row.get(key) for key in ("measure", "verdict", "why", "batches",
                                             "left_out")},
            "r_squared": text(row.get("r_squared")),
            "per_point_of_filler": text(row.get("per_point_of_filler")),
            "wanted": {key: text(value) for key, value in (row.get("wanted") or {}).items()},
            "prediction": None if row["prediction"] is None else {
                key: (value if isinstance(value, bool) else text(value))
                for key, value in row["prediction"].items()},
        } for row in rows])


class FabricSpecificationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = FabricSpecification.objects.select_related(
        "fabric_item", "warp_tape", "weft_tape", "bom"
    )
    serializer_class = FabricSpecificationSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]


class BagSpecificationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BagSpecification.objects.select_related("bag_item", "fabric", "bom")
    serializer_class = BagSpecificationSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]
    # Solving writes nothing, so it asks only to see specifications:
    # whoever quotes a sack need not be allowed to create one.
    action_permission_map = {"solve": "manufacturing.view_bagspecification",
                             "coating": "manufacturing.change_bagspecification"}

    @action(detail=True, methods=["post", "delete"])
    def coating(self, request, pk=None):
        """
        A polymer added to the blend ({"item", "parts"}), or taken out
        (DELETE ?item=). The blend is what makes a sack laminated: the
        first line laminates it, with the coating's weight
        ("lamination_gsm"), and taking the last one off leaves it plain
        and weightless, in the same save. Separately, each refused the
        other: a laminated sack needs a coating, and a coating on a plain
        sack is bought for every sack and put on none.
        """
        sack = self.get_object()
        blend = sack.coating_blend()
        if request.method == "DELETE":
            item = record_or_404(Item, request.query_params.get("item"), "item")
            kept = [(each, parts) for each, parts in blend if each.pk != item.pk]
            if len(kept) == len(blend):
                raise DRFValidationError({"item": [f"{item.sku} is not in the coating."]})
        else:
            line = CoatingLineSerializer(data=request.data)
            line.is_valid(raise_exception=True)
            item, parts = line.validated_data["item"], line.validated_data["parts"]
            if any(each.pk == item.pk for each, _parts in blend):
                raise DRFValidationError({"item": [f"{item.sku} is in the coating already; take it out "
                                                   "and add it with its whole share."]})
            kept = [*blend, (item, parts)]
            if not sack.is_laminated:
                weight = request.data.get("lamination_gsm")
                try:
                    weight = Decimal(str(weight))
                except (InvalidOperation, ValueError):
                    weight = None
                if weight is None or not weight.is_finite() or weight <= 0:
                    raise DRFValidationError({"lamination_gsm": [
                        "The sack becomes laminated with this; say how heavy the coating is."]})
                sack.lamination_gsm = weight
        if not kept:
            sack.lamination_gsm = Decimal("0")
        sack.is_laminated = bool(kept)
        sack.set_coating(kept)
        try:
            with transaction.atomic():
                sack.save()
        except DjangoValidationError as error:
            raise DRFValidationError(error.messages)
        return Response(self.get_serializer(BagSpecification.objects.get(pk=sack.pk)).data)

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
        # The app's own sense checks: not refusals, since a sack outside
        # the usual range can be real, but not to be quoted unread.
        warnings = []
        if not 40 <= gsm <= 200:
            warnings.append(f"Fabric at {gsm:.0f} GSM is outside the usual 40 to 200.")
        for key in ("warp_tape_denier", "weft_tape_denier"):
            if not 300 <= deniers[key] <= 2500:
                warnings.append(f"The {key.split('_')[0]} tape at {deniers[key]:.0f} denier "
                                "is outside the usual 300 to 2,500.")
        return Response({
            "warnings": warnings,
            "target_grams": str(target),
            "addon_grams": str(round(addons, 3)),
            "fabric_area_sqm": str(round(area, 6)),
            "fabric_gsm": str(round(gsm, 3)),
            **{key: str(round(value, 1)) for key, value in deniers.items()},
            "shrink_percent": str(shrink),
            "fabrics": [row for _, _, row in sorted(matches, key=lambda m: m[:2])],
        })


class _DatedRateViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    # A rate is superseded, not edited: no PUT or PATCH. Delete stays,
    # and the model allows it only for a rate not yet in force.
    http_method_names = ["get", "post", "delete", "head", "options"]


class MaterialRateViewSet(_DatedRateViewSet):
    queryset = MaterialRate.objects.select_related("item")
    serializer_class = MaterialRateSerializer
    filter_fields = ["item"]
    search_fields = ["item__sku", "item__name", "note"]
    date_field = "valid_from"
    ordering_fields = ["valid_from"]


class _InwardViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Drafted freely, posted, voided with a reason; never edited once posted."""

    def perform_create(self, serializer):
        _run(serializer.save)

    def perform_update(self, serializer):
        _run(serializer.save)

    def perform_destroy(self, instance):
        _run(instance.delete)

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        document = self.get_object()
        _run(document.post)
        return Response(self.get_serializer(document).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        document = self.get_object()
        _run(document.void, str(request.data.get("reason", "")))
        return Response(self.get_serializer(document).data)


class CustomerMaterialReceiptViewSet(_InwardViewSet):
    """A customer's material arriving on their challan. register/?customer= says
    what is received, used, returned and on hand."""

    queryset = CustomerMaterialReceipt.objects.select_related("customer", "warehouse").prefetch_related(
        "lines__item", "lines__lot")
    serializer_class = CustomerMaterialReceiptSerializer
    filter_fields = ["customer", "warehouse", "posted"]
    search_fields = ["number", "their_challan", "customer__name"]
    date_field = "received_on"
    ordering_fields = ["received_on", "number"]
    action_permission_map = {
        "post": "manufacturing.change_customermaterialreceipt",
        "void": "manufacturing.change_customermaterialreceipt",
    }

    @action(detail=False, methods=["get"])
    def register(self, request):
        from apps.core.models import Party

        customer = get_object_or_404(Party, pk=request.query_params.get("customer"))
        as_of = parse_date(request.query_params.get("as_of", "") or "")
        return Response([{
            "item": row["item"].sku,
            **{key: inward._q(row[key]) for key in ("received", "consumed", "returned",
                                                    "on_hand", "unexplained")},
            "overdue": [late | {"left": inward._q(late["left"])} for late in row["overdue"]],
        } for row in inward.register(customer, as_of=as_of)])


class CustomerMaterialReceiptLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = CustomerMaterialReceiptLine.objects.select_related("receipt")
    serializer_class = CustomerMaterialReceiptLineSerializer
    filter_fields = ["receipt", "item", "receipt__customer", "receipt__posted"]
    search_fields = ["item__sku", "item__name", "lot__code"]

    def perform_create(self, serializer):
        _run(serializer.save)

    def perform_update(self, serializer):
        _run(serializer.save)

    def perform_destroy(self, instance):
        _run(instance.delete)


class CustomerMaterialReturnViewSet(_InwardViewSet):
    queryset = CustomerMaterialReturn.objects.select_related("customer", "warehouse").prefetch_related(
        "lines__item", "lines__lot")
    serializer_class = CustomerMaterialReturnSerializer
    filter_fields = ["customer", "warehouse", "posted"]
    search_fields = ["number", "customer__name"]
    date_field = "returned_on"
    ordering_fields = ["returned_on", "number"]
    action_permission_map = {
        "post": "manufacturing.change_customermaterialreturn",
        "void": "manufacturing.change_customermaterialreturn",
    }


class CustomerMaterialReturnLineViewSet(CustomerMaterialReceiptLineViewSet):
    queryset = CustomerMaterialReturnLine.objects.select_related("material_return")
    serializer_class = CustomerMaterialReturnLineSerializer
    filter_fields = ["material_return"]


class EnergyTariffViewSet(_DatedRateViewSet):
    queryset = EnergyTariff.objects.all()
    serializer_class = EnergyTariffSerializer


def _kwh(value):
    return None if value is None else str(value.quantize(Decimal("0.001")))


def _rupees(value):
    return None if value is None else str(value.quantize(Decimal("0.01")))


class EnergyMeterViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Meters, and idle/?start=&end= for what was drawn with nothing booked."""

    queryset = EnergyMeter.objects.select_related("machine", "work_centre")
    serializer_class = EnergyMeterSerializer
    filter_fields = ["machine", "work_centre"]
    search_fields = ["code"]
    ordering_fields = ["code"]

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """?start&end: each meter's kWh, to runs and idle, cost, and days unread."""
        start, end = _window(request)
        return Response([row | {"kwh": _kwh(row["kwh"]), "run_kwh": _kwh(row["run_kwh"]),
                                "idle_kwh": _kwh(row["idle_kwh"]), "cost": _rupees(row["cost"])}
                         for row in _run(energy.summary, start, end)])

    @action(detail=False, methods=["get"])
    def idle(self, request):
        start = parse_date(request.query_params.get("start", "") or "")
        end = parse_date(request.query_params.get("end", "") or "")
        if start is None or end is None:
            raise DRFValidationError(["Give start and end as YYYY-MM-DD."])
        rows = energy.idle_energy(start, end)
        return Response([row | {"kwh": _kwh(row["kwh"]), "cost": _rupees(row["cost"])}
                         for row in rows])

    @action(detail=True, methods=["get"])
    def balance(self, request, pk=None):
        """Everything the meter recorded, and where it went: runs, and idle."""
        from .orders import WorkOrder

        meter = self.get_object()
        runs = energy.by_run(meter)
        numbers = dict(WorkOrder.objects.filter(pk__in=runs).values_list("pk", "number"))
        idle = sum((kwh for _, kwh in energy.allocation(meter)[1]), Decimal("0"))
        metered = energy.metered_total(meter)
        return Response({
            "metered": _kwh(metered),
            "runs": {numbers[pk]: _kwh(kwh) for pk, kwh in runs.items()},
            "idle": _kwh(idle),
            "not_laid_out": _kwh(metered - sum(runs.values(), Decimal("0")) - idle),
        })


class MeterReadingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Readings are entered and voided; never edited or deleted."""

    queryset = MeterReading.objects.select_related("meter", "shift", "read_by__party")
    serializer_class = MeterReadingSerializer
    filter_fields = ["meter", "shift", "voided_at__isnull"]
    search_fields = ["meter__code"]
    date_field = "shift_date"
    ordering_fields = ["shift_date"]
    http_method_names = ["get", "post", "head", "options"]
    action_permission_map = {"void": "manufacturing.change_meterreading"}

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        reading = self.get_object()
        reading.void(str(request.data.get("reason", "")))
        return Response(MeterReadingSerializer(reading).data)


class StageRateViewSet(_DatedRateViewSet):
    queryset = StageRate.objects.select_related("work_centre")
    serializer_class = StageRateSerializer
    filter_fields = ["stage", "work_centre"]
    search_fields = ["stage", "note"]
    date_field = "valid_from"
    ordering_fields = ["valid_from"]


class QuotePolicyViewSet(_DatedRateViewSet):
    queryset = QuotePolicy.objects.all()
    serializer_class = QuotePolicySerializer
    search_fields = ["note"]
    date_field = "valid_from"
    ordering_fields = ["valid_from"]


class CostSheetViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    A sack costed on a day, frozen. POST costs it; nothing edits it;
    quote/ puts its price on a draft quotation.
    """

    queryset = CostSheet.objects.select_related("specification").prefetch_related("lines")
    serializer_class = CostSheetSerializer
    filter_fields = ["specification", "quotation_line"]
    search_fields = ["specification__code", "specification__name"]
    date_field = "costed_on"
    http_method_names = ["get", "post", "delete", "head", "options"]
    action_permission_map = {"quote": "sales.add_quotationline"}

    def get_queryset(self):
        from apps.sales.scoping import UNLIMITED, carried_by, rep_limit

        queryset = super().get_queryset()
        rep = rep_limit(self.request.user)
        if rep is UNLIMITED:
            return queryset
        # A sack costed for an enquiry is nobody's yet. One priced onto a
        # quotation shows that customer's price, and is a rep's to read
        # only if the customer is theirs.
        return queryset.filter(Q(quotation_line__isnull=True)
                               | carried_by(rep, "quotation_line__quotation__customer"))

    def create(self, request, *args, **kwargs):
        request_data = CostSheetRequestSerializer(data=request.data)
        request_data.is_valid(raise_exception=True)
        data = request_data.validated_data
        sheet = quoting.cost(
            data["specification"], data["quantity"], data.get("costed_on"),
            data.get("margin_percent"),
        )
        return Response(CostSheetSerializer(sheet).data, status=201)

    @action(detail=True, methods=["post"])
    def quote(self, request, pk=None):
        request_data = QuoteRequestSerializer(data=request.data, context={"request": request})
        request_data.is_valid(raise_exception=True)
        line = quoting.quote(self.get_object(), request_data.validated_data["quotation"],
                             request_data.validated_data["taxes"])
        return Response({"quotation_line": line.pk, "unit_price": str(line.unit_price)},
                        status=201)


class TestCertificateViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    Certificates for shipments: POST {delivery} issues one, frozen;
    void/ withdraws it with a reason; print/ is the page sent to the customer.
    """

    queryset = TestCertificate.objects.select_related("delivery__sales_order__customer")
    serializer_class = TestCertificateSerializer
    filter_fields = ["delivery", "voided_at__isnull"]
    search_fields = ["number", "delivery__number", "delivery__sales_order__customer__name"]
    date_field = "issued_on"
    ordering_fields = ["issued_on", "number"]
    http_method_names = ["get", "post", "head", "options"]
    action_permission_map = {"void": "manufacturing.change_testcertificate"}

    def create(self, request, *args, **kwargs):
        from apps.sales.models import Delivery

        delivery = get_object_or_404(Delivery, pk=request.data.get("delivery"))
        certificate = certificates.issue(delivery)
        return Response(TestCertificateSerializer(certificate).data, status=201)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        certificate = self.get_object()
        certificate.void(str(request.data.get("reason", "")))
        return Response(TestCertificateSerializer(certificate).data)

    @action(detail=True, methods=["get"])
    def print(self, request, pk=None):
        from django.template.loader import render_to_string

        certificate = self.get_object()
        page = render_to_string("manufacturing/test_certificate.html",
                                {"certificate": certificate, "c": certificate.content})
        return HttpResponse(page, content_type="text/html; charset=utf-8")


class BillOfMaterialsViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BillOfMaterials.objects.select_related("item", "uom", "routing").prefetch_related(
        "components__item", "components__uom", "components__substitutes",
        "byproducts__item", "byproducts__uom")
    serializer_class = BillOfMaterialsSerializer
    filter_fields = ["item", "is_active", "is_default", "routing", "is_phantom"]
    search_fields = ["item__sku", "item__name", "name"]
    action_permission_map = {"change_order": "manufacturing.add_bomchangeorder"}

    @action(detail=True, methods=["post"], url_path="change-order")
    def change_order(self, request, pk=None):
        """{effective_from, reason}: a new version to edit, and the order that will put it in force."""
        from .changes import raise_change

        order = raise_change(self.get_object(), request.data.get("effective_from"),
                             str(request.data.get("reason", "")), by=request.user)
        return Response({"id": order.pk, "draft": order.draft_id}, status=201)

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
    queryset = BomComponent.objects.select_related("item", "bom", "uom")
    serializer_class = BomComponentSerializer
    filter_fields = ["bom"]


class BomByproductViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BomByproduct.objects.select_related("item", "bom", "uom")
    serializer_class = BomByproductSerializer
    filter_fields = ["bom"]


class BomSubstituteViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BomSubstitute.objects.select_related(
        "item", "component", "component__item", "component__bom"
    )
    serializer_class = BomSubstituteSerializer
    filter_fields = ["component", "component__bom", "item"]


class CostVersionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    Standard costs, rolled and published.

    `publish/` is a POST and it posts a journal entry, because a
    standard cost change is a revaluation of every standard-costed
    shelf in the company.
    """

    queryset = CostVersion.objects.prefetch_related("costs")
    serializer_class = CostVersionSerializer
    search_fields = ["code", "name"]
    date_field = "effective_from"
    ordering_fields = ["effective_from", "code"]
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
    filter_fields = ["version", "item"]
    search_fields = ["item__sku", "item__name"]


class MaintenanceScheduleViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = MaintenanceSchedule.objects.select_related("work_centre", "machine")
    serializer_class = MaintenanceScheduleSerializer
    filter_fields = ["work_centre", "machine", "is_active"]
    search_fields = ["name"]
    ordering_fields = ["name"]
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
        "schedule", "work_centre", "machine", "downtime", "technician__party"
    )
    serializer_class = MaintenanceJobSerializer
    filter_fields = ["work_centre", "machine", "schedule", "is_breakdown", "done_on__isnull",
                     "cancelled_at__isnull"]
    search_fields = ["fault", "notes", "schedule__name"]
    date_field = "due_on"
    ordering_fields = ["due_on"]

    def get_serializer_class(self):
        return MaintenanceJobDetailSerializer if self.action == "retrieve" else MaintenanceJobSerializer
    action_permission_map = {"complete": "manufacturing.change_maintenancejob",
                             "cancel": "manufacturing.change_maintenancejob",
                             "labour": "manufacturing.change_maintenancejob",
                             "breakdown": "manufacturing.add_maintenancejob",
                             "spares": "manufacturing.change_maintenancejob",
                             "return_spares": "manufacturing.change_maintenancejob"}

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """{on_date, minutes} for a service; {on_date, cause, action} for a breakdown."""
        job = self.get_object()
        _run(
            job.complete,
            on_date=request.data.get("on_date"),
            minutes=request.data.get("minutes"),
            cause=request.data.get("cause", ""),
            action=request.data.get("action", ""),
        )
        return Response(self.get_serializer(job).data)

    @action(detail=False, methods=["post"])
    def breakdown(self, request):
        """{downtime, fault, technician?, planned_minutes?}: a repair on a stoppage."""
        from apps.hr.models import Employee

        from .maintenance import raise_breakdown
        from .shifts import Downtime

        data = request.data
        stoppage = get_object_or_404(Downtime, pk=data.get("downtime"))
        technician = (get_object_or_404(Employee, pk=data["technician"])
                      if data.get("technician") else None)
        job = _run(raise_breakdown, stoppage, data.get("fault", ""), technician,
                   data.get("planned_minutes"))
        return Response(self.get_serializer(job).data, status=201)

    @action(detail=True, methods=["post"])
    def labour(self, request, pk=None):
        """{technician, worked_on, minutes}: a fitter's time on an open job."""
        from apps.hr.models import Employee

        from .maintenance import MaintenanceLabour

        job = self.get_object()
        data = request.data
        technician = get_object_or_404(Employee, pk=data.get("technician"))
        try:
            worked_on = parse_date(str(data.get("worked_on") or ""))
            minutes = Decimal(str(data.get("minutes")))
        except (ValueError, InvalidOperation):
            worked_on = minutes = None
        if worked_on is None or minutes is None or not minutes.is_finite():
            raise DRFValidationError(["worked_on is a date, YYYY-MM-DD, and minutes a number."])
        if minutes <= 0:
            raise DRFValidationError(["A fitter's time on a job is more than nothing."])
        row = _run(MaintenanceLabour.objects.create, job=job, technician=technician,
                   worked_on=worked_on, minutes=minutes)
        return Response({"id": row.pk, "job": job.pk, "minutes": str(row.minutes),
                         "labour_minutes": str(job.labour_minutes())}, status=201)

    @action(detail=True, methods=["post"])
    def spares(self, request, pk=None):
        """{warehouse, lines: [{item, quantity, lot?}], issued_to?}: out of the store."""
        from apps.hr.models import Employee
        from apps.inventory.models import Item, Lot, Warehouse

        from .maintenance import issue_spares

        job = self.get_object()
        data = request.data
        warehouse = get_object_or_404(Warehouse, pk=data.get("warehouse"))
        lines = []
        from .positions import MachinePosition

        for row in data.get("lines") or []:
            item = get_object_or_404(Item, pk=row.get("item"))
            lot = get_object_or_404(Lot, pk=row["lot"], item=item) if row.get("lot") else None
            position = get_object_or_404(MachinePosition, pk=row["position"]) if row.get("position") else None
            lines.append((item, row.get("quantity"), lot, position))
        issued_to = (get_object_or_404(Employee, pk=data["issued_to"])
                     if data.get("issued_to") else None)
        issue = _run(issue_spares, job, warehouse, lines, on_date=data.get("on_date"),
                     issued_to=issued_to)
        return Response({"id": issue.pk, "adjustment": issue.adjustment.number,
                         "value": str(issue.value()),
                         "spares_value": str(job.spares_value())}, status=201)

    @action(detail=True, methods=["post"], url_path=r"spares/(?P<issue>[0-9]+)/return")
    def return_spares(self, request, pk=None, issue=None):
        from .maintenance import SpareIssue, return_spares

        job = self.get_object()
        row = get_object_or_404(SpareIssue, pk=issue, job=job)
        _run(return_spares, row, on_date=request.data.get("on_date"))
        return Response({"id": row.pk, "returned": True,
                         "spares_value": str(job.spares_value())})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        job = self.get_object()
        _run(job.cancel, request.data.get("reason", ""))
        return Response(self.get_serializer(job).data)

    @action(detail=False, methods=["get"])
    def reliability(self, request):
        """?start&end and machine= or work_centre=: failures, MTBF, MTTR."""
        from .machines import Machine
        from .maintenance import reliability

        params = request.query_params
        machine = (get_object_or_404(Machine, code=params["machine"])
                   if params.get("machine") else None)
        centre = (get_object_or_404(WorkCentre, code=params["work_centre"])
                  if params.get("work_centre") else None)
        start, end = _window(request)
        found = _run(reliability, start, end, machine=machine, work_centre=centre)
        return Response({key: (str(value) if isinstance(value, Decimal) else value)
                         for key, value in found.items()})


class PrintDesignViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PrintDesign.objects.select_related(
        "customer", "approved_by"
    ).prefetch_related("tools")
    serializer_class = PrintDesignSerializer
    filter_fields = ["is_active", "customer"]
    search_fields = ["code", "name", "customer__name"]
    ordering_fields = ["code"]

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
    filter_fields = ["kind", "status", "work_centre"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]

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
    filter_fields = ["tool"]


class FabricRollViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    Rolls, and the metres on each of them.

    `metres/` is the question the cutting table asks and the one the
    unit-of-measure graph cannot answer, because every roll converts
    at its own weight per metre.
    """

    queryset = FabricRoll.objects.select_related(
        "lot", "lot__item", "specification", "entry", "machine"
    )
    serializer_class = FabricRollSerializer
    filter_fields = ["machine", "specification", "entry"]
    search_fields = ["lot__code"]

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
    queryset = Routing.objects.prefetch_related("operations__work_centre")
    serializer_class = RoutingSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]


class RoutingOperationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = RoutingOperation.objects.select_related("routing", "work_centre")
    serializer_class = RoutingOperationSerializer
    filter_fields = ["routing", "work_centre"]


class SetupFamilyViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Which family an item belongs to on a machine."""

    queryset = SetupFamily.objects.select_related("item", "work_centre").all()
    serializer_class = SetupFamilySerializer
    filter_fields = ["work_centre", "item", "family"]
    search_fields = ["family", "item__sku", "item__name"]


class ChangeoverRuleViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Minutes to change a machine from one family to another."""

    queryset = ChangeoverRule.objects.select_related("work_centre").all()
    serializer_class = ChangeoverRuleSerializer
    filter_fields = ["work_centre"]


class MachineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Named machines: which loom, which extruder, which press."""

    queryset = Machine.objects.select_related("work_centre").all()
    serializer_class = MachineSerializer
    filter_fields = ["work_centre", "is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]


class WorkCentreViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = WorkCentre.objects.all()
    serializer_class = WorkCentreSerializer
    filter_fields = ["is_active", "speed_basis"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]
    # The day's output is the floor's record, not the bank's master data.
    action_permission_map = {"daily": "manufacturing.view_productionentry"}

    @action(detail=False, methods=["get"])
    def daily(self, request):
        """?day= (yesterday if not given): each section's output, scrap, kWh and kWh a kilogramme."""
        from .daily import daily_production

        day = to_date(request.query_params.get("day")) or timezone.localdate() - datetime.timedelta(days=1)
        return Response(daily_production(day))

    @action(detail=True, methods=["get"])
    def crew(self, request, pk=None):
        """?date= : each shift's heads, the machines they can run, and the bank's minutes."""
        from .manning import heads, manned_machines
        from .shifts import Shift

        centre = self.get_object()
        day = parse_date(request.query_params.get("date") or "")
        if day is None:
            raise DRFValidationError(["Give the date as YYYY-MM-DD."])
        return Response({
            "work_centre": centre.code,
            "operators_per_machine": (str(centre.operators_per_machine)
                                      if centre.operators_per_machine is not None else None),
            "shifts": [{"shift": shift.code, "heads": str(heads(centre, shift, day)),
                        "machines_crewed": manned_machines(centre, shift, day)}
                       for shift in Shift.objects.filter(is_active=True)],
            "minutes": str(centre.minutes_on(day)),
        })

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
                    "purge_kg": str(row["purge_kg"]),
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
            "planned_purge_kg": str(result["planned_purge_kg"]),
            "proposed_purge_kg": str(result["proposed_purge_kg"]),
            "saved_purge_kg": str(result["saved_purge_kg"]),
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
    search_fields = ["number", "item__sku", "item__name"]
    filter_fields = ["status", "item", "work_centre", "warehouse", "sales_order_line"]
    date_field = "scheduled_start"
    ordering_fields = ["scheduled_start", "number"]

    queryset = WorkOrder.objects.select_related(
        "item", "bom", "warehouse", "work_centre"
    ).prefetch_related(Prefetch("components", queryset=WorkOrderComponent.objects.select_related("item")))
    serializer_class = WorkOrderSerializer

    def get_serializer_class(self):
        return WorkOrderListSerializer if self.action == "list" else WorkOrderSerializer
    action_permission_map = {
        "choose_routing": "manufacturing.change_workorder",
        "release": "manufacturing.change_workorder",
        "close": "manufacturing.change_workorder",
        "reopen": "manufacturing.change_workorder",
        "cancel": "manufacturing.change_workorder",
        "board": "manufacturing.view_workorder",
    }

    @action(detail=False, methods=["get"])
    def board(self, request):
        """The runs in columns: not released, waiting, running, output complete, closed this week."""
        from .board import board

        return Response(board())

    @action(detail=True, methods=["get"])
    def waste(self, request, pk=None):
        """What the run's recipe expected back as waste, and what was weighed."""
        from .station_floor import waste_variance

        return Response([{"item": row["item"].sku, "expected": str(row["expected"]),
                          "weighed": str(row["weighed"]), "difference": str(row["difference"])}
                         for row in waste_variance(self.get_object())])

    @action(detail=True, methods=["get"])
    def traveller(self, request, pk=None):
        """The job card, A4, to print and send round the floor with the run."""
        from django.template.loader import render_to_string

        from .traveller import traveller

        order = self.get_object()
        try:
            card = traveller(order)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        page = render_to_string("manufacturing/traveller.html", {"t": card})
        return HttpResponse(page, content_type="text/html; charset=utf-8")

    @action(detail=True, methods=["get"])
    def energy(self, request, pk=None):
        """What the meters say this run drew. Read, never posted."""
        found = energy.run_energy(self.get_object())
        return Response(found | {
            "kwh": _kwh(found["kwh"]), "cost": _rupees(found["cost"]),
            "kwh_per_unit": (None if found["kwh_per_unit"] is None
                             else str(found["kwh_per_unit"].quantize(Decimal("0.000001")))),
            "standard_kwh": _kwh(found["standard_kwh"]),
            "variance_kwh": _kwh(found["variance_kwh"]),
            "metered_minutes": str(found["metered_minutes"]),
            "unmetered_minutes": str(found["unmetered_minutes"]),
        })

    def _reply(self, order):
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        order = self.get_object()
        _run(order.release, on_date=request.data.get("on_date"))
        return self._reply(order)

    @action(detail=True, methods=["post"], url_path="choose-routing")
    def choose_routing(self, request, pk=None):
        """{routing}: a draft run made one of its bill's other ways."""
        order = self.get_object()
        routing = get_object_or_404(Routing, pk=request.data.get("routing"))
        _run(order.choose_routing, routing)
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
        "work_order__item", "warehouse"
    ).prefetch_related("lines__item", "lines__uom", "lines__lot")
    serializer_class = MaterialIssueSerializer
    filter_fields = ["work_order", "posted", "direction", "warehouse"]
    search_fields = ["number", "work_order__number", "work_order__item__sku", "memo"]
    date_field = "issue_date"
    ordering_fields = ["issue_date", "number"]
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
    queryset = MaterialIssueLine.objects.select_related("item", "issue", "uom", "lot")
    serializer_class = MaterialIssueLineSerializer
    filter_fields = ["issue"]


class ProductionEntryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ProductionEntry.objects.select_related(
        "work_order__item", "warehouse", "work_centre", "uom", "lot", "machine"
    ).prefetch_related("byproducts__item", "byproducts__uom", "byproducts__lot")
    serializer_class = ProductionEntrySerializer
    filter_fields = ["work_order", "posted", "machine", "work_centre", "warehouse"]
    search_fields = ["number", "work_order__number", "work_order__item__sku", "memo"]
    date_field = "entry_date"
    ordering_fields = ["entry_date", "number"]
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
    queryset = ProductionByproduct.objects.select_related("item", "entry", "uom", "lot")
    serializer_class = ProductionByproductSerializer
    filter_fields = ["entry"]


class TimeBookingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TimeBooking.objects.select_related(
        "work_order", "operation", "operation__work_centre", "shift", "machine"
    )
    serializer_class = TimeBookingSerializer
    filter_fields = ["work_order", "posted", "machine", "shift", "operation"]
    search_fields = ["number", "work_order__number", "memo"]
    date_field = "booking_date"
    ordering_fields = ["booking_date", "number"]
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


class WorkOrderOperationViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """
    A run's steps, to pick one when booking time against it. Read-only:
    the steps come from the routing when the run is released.
    """

    queryset = WorkOrderOperation.objects.select_related("work_order__item").order_by(
        "work_order__number", "sequence")
    serializer_class = OperationChoiceSerializer
    filter_fields = ["work_order", "work_centre", "work_order__status", "is_outside"]
    search_fields = ["work_order__number", "name", "work_order__item__sku"]


class ShiftViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Shift.objects.all()
    serializer_class = ShiftSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]


class DowntimeReasonViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = DowntimeReason.objects.all()
    filter_fields = ["is_planned", "is_active"]
    serializer_class = DowntimeReasonSerializer
    search_fields = ["code", "name"]


class DowntimeViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Downtime.objects.select_related(
        "work_centre", "machine", "shift", "reason", "work_order"
    )
    serializer_class = DowntimeSerializer
    filter_fields = ["work_centre", "machine", "shift", "reason", "work_order", "reason__is_planned"]
    search_fields = ["number", "notes"]
    date_field = "shift_date"
    ordering_fields = ["shift_date", "minutes", "number"]

    @action(detail=False, methods=["get"], url_path="by-reason")
    def by_reason(self, request):
        """
        ?start&end, and work_centre= or machine= (codes): stoppages by reason,
        the most hours first, each with its share of all the hours stopped.
        """
        from django.db.models import Count, Sum

        start, end = _window(request)
        rows = Downtime.objects.filter(shift_date__gte=start, shift_date__lte=end)
        params = request.query_params
        if params.get("machine"):
            rows = rows.filter(machine=get_object_or_404(Machine, code=params["machine"]))
        elif params.get("work_centre"):
            rows = rows.filter(work_centre=get_object_or_404(WorkCentre, code=params["work_centre"]))
        grouped = list(rows.values("reason__code", "reason__name", "reason__is_planned").annotate(
            stoppages=Count("id"), minutes=Sum("minutes")).order_by("-minutes", "reason__code"))
        # At the column's two places on every database: SQLite sums 120,
        # PostgreSQL 120.00.
        cents = Decimal("0.01")
        total = sum((row["minutes"] for row in grouped), Decimal("0")).quantize(cents)
        return Response({
            "rows": [{
                "reason": row["reason__code"], "name": row["reason__name"],
                "planned": row["reason__is_planned"], "stoppages": row["stoppages"],
                "minutes": str(row["minutes"].quantize(cents)),
                "share": str((row["minutes"] * 100 / total).quantize(Decimal("0.1"))) if total else "0.0",
            } for row in grouped],
            "total_minutes": str(total),
        })


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
                "minutes": str(Decimal(row["minutes"]).quantize(Decimal("0.01"))),
                "ideal_minutes": str(Decimal(row["ideal_minutes"]).quantize(Decimal("0.01"))),
                "performance": (None if row["performance"] is None
                                else str(row["performance"].quantize(Decimal("0.0001")))),
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
                "from_lot": _lot_row(row["from_lot"]), "quantity": _stock_text(row["quantity"]),
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
                    "used_by": row["used_by"].number, "quantity": _stock_text(row["quantity"]),
                    "made": [_lot_row(child) for child in row["made"]],
                }
                for row in report["descendants"]
            ],
            "customers": [
                {
                    "customer": row["customer"].code, "name": row["customer"].name,
                    "lot": _lot_row(row["lot"]), "quantity": _stock_text(row["quantity"]),
                    "deliveries": row["deliveries"],
                }
                for row in report["customers"]
            ],
            "bales": report["bales"],
            "not_followed": [_lot_row(lot) for lot in report["not_followed"]],
        })

    @action(detail=True, methods=["get"])
    def rolls(self, request, pk=None):
        """A bundle's rolls: the one it was cut from, back to the stock roll under it."""
        from .conversion import BagCount
        from .process_rolls import roll_chain

        lot = self._lot(pk)
        count = BagCount.objects.select_related("mount", "machine").filter(
            inspection__lot=lot).first()
        if count is None:
            raise DRFValidationError([f"{lot.code} is not a bundle counted at a station."])
        chain = roll_chain(count.mount) if count.mount_id else []
        under = Lot.objects.filter(code=chain[-1]["code"]).first() if chain else None
        return Response({"lot": _lot_row(lot), "cut_on": count.machine.code, "rolls": chain,
                         "doffs": self._doffs(under) if under is not None else []})

    @staticmethod
    def _doffs(lot):
        from .rolls import FabricRoll
        from .tape_loads import tape_for

        roll = FabricRoll.objects.filter(lot=lot).select_related("entry").first()
        if roll is None:
            return []
        return [{"doff": load.lot.code, "side": load.side, "kg": str(load.kg),
                 "loaded_at": load.loaded_at} for load in tape_for(roll)]

    @action(detail=True, methods=["get"])
    def doffs(self, request, pk=None):
        """A fabric roll's doffs: the tape on its loom while it was woven."""
        lot = self._lot(pk)
        return Response({"lot": _lot_row(lot), "doffs": self._doffs(lot)})



class JobWorkChallanViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    Challans for goods going out to a job worker. Drafted and edited
    freely; issued with `post`, withdrawn with `void`, never edited once
    issued.
    """

    queryset = JobWorkChallan.objects.select_related("job_worker").prefetch_related(
        "lines__operation__work_order")
    action_permission_map = {"send": "manufacturing.change_jobworkchallan"}
    serializer_class = JobWorkChallanSerializer
    filter_fields = ["job_worker", "posted"]
    search_fields = ["number", "job_worker__name", "vehicle"]
    date_field = "challan_date"
    ordering_fields = ["challan_date", "number"]
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
        recipient = document.email_to_job_worker(to=request.data.get("to") or None, subject=request.data.get("subject") or None,
                                 body=request.data.get("body") or None, user=request.user)
        return Response({"sent_to": recipient})

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
    filter_fields = ["challan"]

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
    filter_fields = ["line", "line__challan"]
    date_field = "loss_date"
    http_method_names = ["get", "post", "head", "options"]
    action_permission_map = {"void": "manufacturing.change_jobworkloss"}

    def perform_create(self, serializer):
        _run(serializer.save)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        loss = self.get_object()
        loss.void()
        return Response(JobWorkLossSerializer(loss).data)


class DispatchViewSet(viewsets.ViewSet):
    """
    The detailed schedule by machine. Reading it changes nothing;
    committing it writes each operation's machine and planned times.
    """

    permission_classes = [IsAuthenticated, RequiredPermission, ActionPermission]

    required_permission = "manufacturing.view_workorder"
    action_permission_map = {"commit": "manufacturing.change_workorderoperation"}

    @staticmethod
    def _row(row):
        order = row["order"]
        return {
            "work_order": order.pk, "run": order.number, "item": order.item.sku,
            "operation": row["operation"].name, "sequence": row["operation"].sequence,
            "start": row["start"], "finish": row["finish"],
            "changeover_minutes": str(row["changeover"]), "purge_kg": str(row["purge_kg"]),
            "late": row["late"],
            "due": order.scheduled_end, "held": row["held"], "moved_from": row["moved_from"],
        }

    def list(self, request):
        from .dispatch import build, dispatch_list

        board = dispatch_list(build())
        return Response({code: [self._row(row) for row in rows] for code, rows in board.items()})

    @action(detail=False, methods=["post"])
    def commit(self, request):
        from .dispatch import build, commit

        return Response({"committed": _run(commit, build())})


class BaleViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Bales: POST pack/ {warehouse, packed_by, lines: [{lot, quantity}], gross_kg} presses
    and seals one; {id}/break/ with a reason frees its bundles; load/ {delivery,
    bales, order_line} puts sealed bales on a draft delivery and {id}/unload/ takes
    one off; {id}/label/ is the 100 x 75 mm label; {id}/trace/ reads it both ways.
    """

    queryset = Bale.objects.select_related(
        "item", "warehouse", "packed_by__party", "delivery", "packing__adjustment",
    ).prefetch_related(
        Prefetch("lines", queryset=BaleLine.objects.select_related("lot")),
        Prefetch("packing__adjustment__lines", queryset=StockAdjustmentLine.objects.select_related("item", "movement")),
    )
    action_permission_map = {
        "break_": "manufacturing.change_bale",
        "load": "manufacturing.change_bale",
        "unload": "manufacturing.change_bale",
    }

    def _row(self, bale):
        return {
            "id": bale.pk, "number": bale.number, "item": bale.item.sku,
            "warehouse": bale.warehouse.code, "packed_on": bale.packed_on,
            "packed_by": bale.packed_by.employee_number, "status": bale.status(),
            "bags": plain(bale.bags()), "nominal_kg": bale.nominal_kg(), "gross_kg": bale.gross_kg,
            "delivery": bale.delivery.number if bale.delivery_id else None,
            "lines": [{"lot": line.lot.code, "bags": plain(line.quantity)} for line in bale.lines.all()],
            "packing": [{"item": item.sku, "quantity": plain(quantity)} for item, quantity in bale.packing.lines()]
            if hasattr(bale, "packing") else [],
            "packing_cost": bale.packing.cost() if hasattr(bale, "packing") else None,
        }

    def list(self, request):
        return Response([self._row(bale) for bale in self.get_queryset()[:200]])

    def retrieve(self, request, pk=None):
        return Response(self._row(self.get_object()))

    @action(detail=False, methods=["post"])
    def pack(self, request):
        from apps.hr.models import Employee

        from .bales import pack

        data = request.data
        warehouse = get_object_or_404(Warehouse, pk=data.get("warehouse"))
        packed_by = get_object_or_404(Employee, pk=data.get("packed_by"))
        rows = [(get_object_or_404(Lot, pk=row.get("lot")), row.get("quantity"))
                for row in data.get("lines") or []]
        bale = _run(pack, warehouse, packed_by, rows, on_date=data.get("packed_on"),
                    gross_kg=data.get("gross_kg"))
        return Response(self._row(bale), status=201)

    @action(detail=True, methods=["post"], url_path="break")
    def break_(self, request, pk=None):
        from .bales import break_bale

        bale = self.get_object()
        _run(break_bale, bale, str(request.data.get("reason", "")))
        return Response(self._row(bale))

    @action(detail=False, methods=["get"])
    def scan(self, request):
        """?number=: the bale whose label was scanned, as its row; 404 where no bale carries that number."""
        number = (request.query_params.get("number") or "").strip()
        bale = self.get_queryset().filter(number__iexact=number).first() if number else None
        if bale is None:
            raise NotFound(f"No bale is numbered {number!r}.")
        return Response(self._row(bale))

    @action(detail=False, methods=["post"])
    def load(self, request):
        from apps.sales.models import Delivery, SalesOrderLine

        from .bales import Bale, load

        delivery = get_object_or_404(Delivery, pk=request.data.get("delivery"))
        bales = [get_object_or_404(Bale, pk=pk) for pk in request.data.get("bales") or []]
        order_line = (get_object_or_404(SalesOrderLine, pk=request.data["order_line"])
                      if request.data.get("order_line") else None)
        if not bales:
            raise DRFValidationError(["Name the bales to load."])
        _run(load, delivery, bales, order_line=order_line)
        return Response([self._row(Bale.objects.get(pk=bale.pk)) for bale in bales])

    @action(detail=True, methods=["post"])
    def unload(self, request, pk=None):
        from .bales import unload

        return Response(self._row(_run(unload, self.get_object())))

    @action(detail=True, methods=["get"])
    def trace(self, request, pk=None):
        from .bales import trace

        found = trace(self.get_object())
        found["bags"] = plain(found["bags"])
        for bundle in found["bundles"]:
            bundle["bags"] = plain(bundle["bags"])
            for key in ("mean_grams", "target_grams"):
                bundle[key] = None if bundle[key] is None else str(bundle[key])
        return Response(found)

    @action(detail=True, methods=["get"])
    def label(self, request, pk=None):
        """The bale's own label: its number as a barcode, and what is in it."""
        from django.utils.html import escape

        from . import barcode

        bale = self.get_object()
        if bale.broken_at is not None:
            raise DRFValidationError([f"{bale} was broken; it has no label."])
        bundles = ", ".join(f"{line.lot.code} x {plain(line.quantity)}"
                            for line in bale.lines.select_related("lot"))
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{escape(bale.number)}</title>
<style>
  @page {{ size: 100mm 75mm; margin: 0; }}
  html, body {{ margin: 0; background: #fff; color: #000; }}
  .label {{ width: 100mm; height: 75mm; box-sizing: border-box; padding: 4mm 5mm;
           display: flex; flex-direction: column; gap: 1.5mm; font: 3mm/1.25 system-ui, sans-serif; }}
  .bars {{ height: 22mm; }} .bars svg {{ width: 100%; height: 100%; display: block; }}
  .code {{ font: 600 6mm/1 ui-monospace, monospace; }}
  .big {{ font-size: 5mm; font-weight: 600; }}
</style></head>
<body><div class="label">
<div class="bars">{barcode.svg(bale.number)}</div>
<div class="code">{escape(bale.number)}</div>
<div class="big">{escape(bale.item.name)} &middot; {plain(bale.bags())} bags</div>
<div>Nominal {bale.nominal_kg()} kg{f" &middot; weighed {bale.gross_kg} kg" if bale.gross_kg else ""}
 &middot; packed {bale.packed_on:%d %b %Y} by {escape(bale.packed_by.employee_number)}</div>
<div>Bundles: {escape(bundles)}</div>
</div></body></html>"""
        return HttpResponse(page, content_type="text/html; charset=utf-8")


def _stock_text(value):
    return format(Decimal(value).normalize(), "f") if value is not None else None


class ScrapReasonViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ScrapReason.objects.all()
    serializer_class = ScrapReasonSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]


class ProductionScrapViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Why and where an entry's scrap failed. Written while the entry is a draft."""

    queryset = ProductionScrap.objects.select_related("entry", "reason", "operation")
    serializer_class = ProductionScrapSerializer
    filter_fields = ["entry", "reason"]

    def perform_create(self, serializer):
        _run(serializer.save)

    def perform_update(self, serializer):
        _run(serializer.save)

    def perform_destroy(self, instance):
        _run(instance.delete)


class OperationReportViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """What each step of a run passed on, counted before it moved."""

    queryset = OperationReport.objects.select_related("operation__work_order")
    serializer_class = OperationReportSerializer
    filter_fields = ["operation", "operation__work_order", "machine"]
    search_fields = ["operation__work_order__number", "operation__name", "memo"]
    date_field = "reported_on"

    @action(detail=False, methods=["post"])
    def record(self, request):
        operation = get_object_or_404(WorkOrderOperation, pk=request.data.get("operation"))
        machine = None
        if request.data.get("machine"):
            machine = get_object_or_404(Machine, pk=request.data.get("machine"))
        try:
            quantity = Decimal(str(request.data.get("quantity_good")))
        except InvalidOperation:
            raise DRFValidationError(["quantity_good is a number."])
        counted = _run(scrap.report, operation, quantity,
                       on_date=request.data.get("reported_on"), machine=machine,
                       memo=request.data.get("memo") or "")
        return Response(self.get_serializer(counted).data, status=201)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        counted = self.get_object()
        _run(counted.void, request.data.get("reason", ""))
        return Response(self.get_serializer(counted).data)


class RunFlowViewSet(viewsets.ViewSet):
    """GET run-flow/{work order id}/: each step's good, scrap and what waits before it;
    GET run-flow/scrap/?start=&end=: scrap by item, step and reason."""

    permission_classes = [IsAuthenticated, RequiredPermission, ActionPermission]

    required_permission = "manufacturing.view_workorder"

    def retrieve(self, request, pk=None):
        order = get_object_or_404(WorkOrder, pk=pk)
        return Response([{
            **row, "good": _stock_text(row["good"]), "scrap": _stock_text(row["scrap"]),
            "waiting_before": _stock_text(row["waiting_before"]),
            "scrap_by_reason": {key: _stock_text(value)
                                for key, value in row["scrap_by_reason"].items()},
        } for row in scrap.flow(order)])

    @action(detail=False, methods=["get"], url_path="scrap")
    def scrap_report(self, request):
        start, end = request.query_params.get("start"), request.query_params.get("end")
        if not start or not end:
            raise DRFValidationError(["Give start and end dates."])
        rows = _run(scrap.scrap_report, start, end)
        return Response([{**row, "quantity": _stock_text(row["quantity"])} for row in rows])


class RebatchViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Batches split or joined: record (posted at once) and void."""

    queryset = Rebatch.objects.select_related("item", "warehouse").prefetch_related("lines__lot")
    serializer_class = RebatchSerializer
    filter_fields = ["item", "warehouse", "posted"]
    search_fields = ["number", "reason", "item__sku"]
    date_field = "rebatched_on"
    ordering_fields = ["rebatched_on", "number"]

    @action(detail=False, methods=["post"])
    def record(self, request):
        from django.db import transaction

        from apps.inventory.models import Item, Lot, Warehouse

        data = request.data
        item = get_object_or_404(Item, pk=data.get("item"))
        warehouse = get_object_or_404(Warehouse, pk=data.get("warehouse"))
        try:
            taken = [(get_object_or_404(Lot, pk=row.get("lot")), Decimal(str(row.get("quantity"))))
                     for row in data.get("taken") or []]
            made_rows = [(str(row.get("code") or "").strip(), Decimal(str(row.get("quantity"))))
                         for row in data.get("made") or []]
        except (InvalidOperation, AttributeError):
            raise DRFValidationError(["Each row gives a batch and a quantity."])
        if any(not code for code, _ in made_rows):
            raise DRFValidationError(["Each new batch needs a code."])

        def record_it():
            with transaction.atomic():
                # New batches by code; one that exists and has held stock is
                # refused by the re-batch itself.
                made = [(Lot.objects.get_or_create(item=item, code=code)[0], quantity)
                        for code, quantity in made_rows]
                return rebatching.rebatch(item, warehouse, taken, made, data.get("reason"),
                                          on_date=data.get("rebatched_on"))

        document = _run(record_it)
        return Response(self.get_serializer(document).data, status=201)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        document = self.get_object()
        _run(document.void, request.data.get("reason", ""))
        return Response(self.get_serializer(document).data)
