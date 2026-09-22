"""
The planning API.

`runs/plan/` is the one that matters: it is a POST because running the
plan writes a run, and a planner who could refresh a page into a
hundred stored plans would rightly stop using it.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin
from apps.core.models import Party
from apps.inventory.models import Warehouse

from .levels import low_level_codes
from .models import PlannedDemand, PlannedOrder, PlanningRun, PlanningSettings
from .mrp import plan
from .serializers import (
    PlannedDemandSerializer,
    PlannedOrderSerializer,
    PlanningRunSerializer,
    PlanningSettingsSerializer,
)


def _run(callable_, *args, **kwargs):
    """Let a model's refusal reach the caller as the sentence it wrote."""
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


class PlanningSettingsViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PlanningSettings.objects.select_related("requisition_requester")
    serializer_class = PlanningSettingsSerializer


class PlanningRunViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PlanningRun.objects.select_related("warehouse").prefetch_related(
        "orders"
    )
    serializer_class = PlanningRunSerializer
    action_permission_map = {"plan": "planning.add_planningrun"}

    @action(detail=False, methods=["post"])
    def plan(self, request):
        warehouse = _run(
            Warehouse.objects.filter(pk=request.data.get("warehouse")).first
        )
        if warehouse is None:
            raise DRFValidationError(["Name a warehouse to plan."])
        horizon = request.data.get("horizon_days")
        run = _run(
            plan, warehouse,
            planned_on=request.data.get("planned_on") or None,
            horizon_days=int(horizon) if horizon not in (None, "") else None,
        )
        return Response(PlanningRunSerializer(run).data)

    @action(detail=True, methods=["get"])
    def orders(self, request, pk=None):
        run = self.get_object()
        return Response(
            PlannedOrderSerializer(
                run.orders.select_related("item", "warehouse", "vendor", "bom")
                .prefetch_related("demands"),
                many=True,
            ).data
        )


class PlannedOrderViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PlannedOrder.objects.select_related(
        "run", "item", "warehouse", "vendor", "bom", "work_order",
        "requisition_line",
    ).prefetch_related("demands")
    serializer_class = PlannedOrderSerializer
    action_permission_map = {
        "firm": "planning.change_plannedorder",
        "cancel": "planning.change_plannedorder",
    }

    @action(detail=True, methods=["post"])
    def firm(self, request, pk=None):
        order = self.get_object()
        requester = Party.objects.filter(
            pk=request.data.get("requested_by")
        ).first()
        _run(order.firm, requested_by=requester)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        _run(order.cancel)
        return Response(self.get_serializer(order).data)


class PlannedDemandViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = PlannedDemand.objects.select_related(
        "planned_order", "sales_order_line", "work_order", "parent"
    )
    serializer_class = PlannedDemandSerializer


class LowLevelCodeViewSet(viewsets.ViewSet):
    """
    Where every item sits in the explosion, and what had to be cut to
    say so.

    Read-only and derived: a stored level is the classic drifting copy,
    wrong from the moment somebody edits a bill of materials.
    """

    queryset = PlannedOrder.objects.none()

    def list(self, request):
        from apps.inventory.models import Item

        levels = low_level_codes()
        items = {
            item.pk: item
            for item in Item.objects.filter(pk__in=levels.code_of)
        }
        return Response({
            "levels": sorted(
                (
                    {"item": pk, "sku": items[pk].sku, "level": code}
                    for pk, code in levels.code_of.items()
                    if pk in items
                ),
                key=lambda row: (row["level"], row["sku"]),
            ),
            "cuts": [
                {"item": cut.item.sku, "parent": cut.parent.sku}
                for cut in levels.cuts
            ],
        })
