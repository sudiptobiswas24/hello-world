from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin
from apps.inventory.models import Lot

from .models import (
    Characteristic,
    Inspection,
    InspectionPlan,
    PlanLine,
    Reading,
)
from .calibration import Calibration, Instrument, due
from .release import latest_inspection, release_status
from .serializers import (
    CalibrationSerializer,
    InstrumentSerializer,
    CharacteristicSerializer,
    InspectionPlanSerializer,
    InspectionSerializer,
    PlanLineSerializer,
    ReadingSerializer,
)


def _run(callable_, *args, **kwargs):
    """Let a model's refusal reach the caller as the sentence it wrote."""
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


class CharacteristicViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Characteristic.objects.select_related("uom")
    serializer_class = CharacteristicSerializer
    filter_fields = ["is_active", "kind"]
    search_fields = ["code", "name"]
    ordering_fields = ["code"]


class InspectionPlanViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = InspectionPlan.objects.select_related("item").prefetch_related("lines__characteristic",
        "lines"
    )
    serializer_class = InspectionPlanSerializer
    filter_fields = ["is_active", "item", "is_mandatory"]
    search_fields = ["name", "item__sku", "item__name"]
    ordering_fields = ["name"]


class PlanLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PlanLine.objects.select_related("plan", "characteristic")
    serializer_class = PlanLineSerializer
    filter_fields = ["plan"]


class InspectionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Inspection.objects.select_related(
        "lot", "lot__item", "plan", "inspected_by", "decided_by"
    ).prefetch_related("readings__plan_line__characteristic")
    serializer_class = InspectionSerializer
    filter_fields = ["posted", "result", "lot", "plan", "disposition"]
    search_fields = ["number", "lot__code", "lot__item__sku", "lot__item__name"]
    date_field = "inspected_on"
    ordering_fields = ["inspected_on", "number"]
    action_permission_map = {
        "post": "quality.change_inspection",
        "void": "quality.change_inspection",
    }

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        inspection = self.get_object()
        _run(inspection.post)
        return Response(self.get_serializer(inspection).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        inspection = self.get_object()
        _run(inspection.void, reason=request.data.get("reason", ""))
        return Response(self.get_serializer(inspection).data)


class ReadingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Reading.objects.select_related("inspection", "plan_line")
    serializer_class = ReadingSerializer
    filter_fields = ["inspection", "plan_line"]


class LotStatusViewSet(viewsets.ViewSet):
    """
    Where a batch stands, and what said so.

    The question a picker asks before putting a roll on a lorry, and the
    one a planner asks before drawing it into a run.
    """

    queryset = Inspection.objects.none()

    def retrieve(self, request, pk=None):
        lot = Lot.objects.filter(pk=pk).select_related("item").first()
        if lot is None:
            raise DRFValidationError([f"No batch with id {pk}."])
        latest = latest_inspection(lot)
        return Response({
            "lot": lot.code,
            "item": lot.item.sku,
            "status": release_status(lot),
            "inspection": latest.number if latest else None,
            "inspected_on": latest.inspected_on if latest else None,
            "result": latest.result if latest else None,
            "disposition": latest.disposition if latest else None,
        })


class InstrumentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Scales and testers. GET due/?within=30: what needs calibrating."""

    queryset = Instrument.objects.prefetch_related("measures")
    serializer_class = InstrumentSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name", "serial_number"]
    ordering_fields = ["code"]

    @action(detail=False, methods=["get"])
    def due(self, request):
        try:
            within = int(request.query_params.get("within", 30))
        except ValueError:
            raise DRFValidationError(["within is a number of days."])
        return Response([{
            "instrument": row["instrument"].code, "status": row["status"],
            "due_on": str(row["due_on"]) if row["due_on"] else None,
        } for row in due(within, request.query_params.get("on"))])


class CalibrationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Drafted, then posted; a posted one is voided, never edited."""

    queryset = Calibration.objects.select_related("instrument")
    serializer_class = CalibrationSerializer
    filter_fields = ["instrument", "posted", "result"]
    search_fields = ["number", "instrument__code", "instrument__name"]
    date_field = "calibrated_on"
    ordering_fields = ["calibrated_on", "number"]
    # Posting decides which inspections are now in doubt; voiding takes that
    # back. The Inspector records one; the manager decides it (as the screen
    # has always shown it).
    action_permission_map = {"post": "quality.change_calibration", "void": "quality.change_calibration"}

    def perform_update(self, serializer):
        _run(serializer.save)

    def perform_destroy(self, instance):
        _run(instance.delete)

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        calibration = self.get_object()
        _run(calibration.post)
        return Response(self.get_serializer(calibration).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        calibration = self.get_object()
        _run(calibration.void, request.data.get("reason", ""))
        return Response(self.get_serializer(calibration).data)

    @action(detail=True, methods=["get"])
    def suspects(self, request, pk=None):
        """Inspections nobody can now vouch for, where this found it out of tolerance."""
        calibration = self.get_object()
        return Response([{
            "inspection": inspection.number, "lot": inspection.lot.code,
            "inspected_on": str(inspection.inspected_on),
        } for inspection in calibration.suspect_inspections()])


class ControlChartViewSet(viewsets.ViewSet):
    """GET spc/?item=&characteristic=CODE&start=&end=&baseline_end=: X-bar and R (or
    individuals) for one characteristic of one item, with signals and capability."""

    queryset = Inspection.objects.none()

    def list(self, request):
        from django.shortcuts import get_object_or_404

        from apps.inventory.models import Item

        from .spc import chart

        params = request.query_params
        item = get_object_or_404(Item, pk=params.get("item"))
        characteristic = get_object_or_404(Characteristic, code=params.get("characteristic"))
        found = _run(chart, item, characteristic, params.get("start"), params.get("end"),
                     params.get("baseline_end"))

        def text(value):
            return None if value is None else format(round(float(value), 4), ".4f")

        capability = found["capability"]
        return Response({
            **found, "centre": text(found["centre"]), "sigma": text(found["sigma"]),
            "subgroup_sizes": {str(k): v for k, v in found["subgroup_sizes"].items()},
            "points": [{
                **point, "inspected_on": str(point["inspected_on"]),
                **{key: text(point.get(key)) for key in (
                    "mean", "range", "ucl", "lcl", "r_ucl", "r_lcl", "moving_range")
                   if key in point},
            } for point in found["points"]],
            "capability": None if capability is None else {
                key: text(value) for key, value in capability.items()},
        })


class SamplingPlanViewSet(viewsets.ViewSet):
    """GET sampling/?lot_size=&level=II&aql=2.5: how many to pull and how many may fail."""

    queryset = Inspection.objects.none()

    def list(self, request):
        from .sampling import sampling_plan

        params = request.query_params
        try:
            lot_size = int(params.get("lot_size", ""))
        except ValueError:
            raise DRFValidationError(["lot_size is a whole number of units."])
        return Response(_run(sampling_plan, lot_size, params.get("level", "II"),
                             params.get("aql", "")))
