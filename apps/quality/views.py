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
from .release import latest_inspection, release_status
from .serializers import (
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


class InspectionPlanViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = InspectionPlan.objects.select_related("item").prefetch_related(
        "lines"
    )
    serializer_class = InspectionPlanSerializer


class PlanLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PlanLine.objects.select_related("plan", "characteristic")
    serializer_class = PlanLineSerializer


class InspectionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Inspection.objects.select_related(
        "lot", "lot__item", "plan", "inspected_by", "decided_by"
    ).prefetch_related("readings")
    serializer_class = InspectionSerializer
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
