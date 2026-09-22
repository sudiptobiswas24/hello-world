"""
The planning API.

`runs/plan/` is the one that matters: it is a POST because running the
plan writes a run, and a planner who could refresh a page into a
hundred stored plans would rightly stop using it.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin
from apps.core.models import Party, to_date
from apps.inventory.models import Warehouse

from .levels import low_level_codes
from .forecast import Forecast, coverage
from .models import (
    PlannedDemand,
    PlannedOrder,
    PlanningAction,
    PlanningRun,
    PlanningSettings,
    TransferRoute,
)
from .mrp import plan
from .serializers import (
    ForecastSerializer,
    TransferRouteSerializer,
    PlannedDemandSerializer,
    PlanningActionSerializer,
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


class TransferRouteViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TransferRoute.objects.select_related(
        "from_warehouse", "to_warehouse"
    )
    serializer_class = TransferRouteSerializer


class ForecastViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Forecast.objects.select_related("item", "warehouse")
    serializer_class = ForecastSerializer

    @action(detail=False, methods=["get"])
    def coverage(self, request):
        """Forecast against orders, period by period."""
        from apps.inventory.models import Item, Warehouse

        item = Item.objects.filter(pk=request.query_params.get("item")).first()
        warehouse = Warehouse.objects.filter(
            pk=request.query_params.get("warehouse")
        ).first()
        if item is None or warehouse is None:
            raise DRFValidationError(["Name an item and a warehouse."])
        return Response([
            {
                "forecast": row["forecast"].pk,
                "starts_on": row["period"][0],
                "ends_on": row["period"][1],
                "expected": row["expected"],
                "ordered": row["ordered"],
                "unconsumed": row["unconsumed"],
                "over_ordered": row["over_ordered"],
                "accuracy_percent": row["accuracy_percent"],
            }
            for row in _run(
                coverage, item, warehouse,
                planned_on=to_date(request.query_params.get("on")) or None,
            )
        ])


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
    def load(self, request, pk=None):
        """
        What each machine is being asked to do against what it can,
        week by week.

        Weekly because that is the grain a planner acts on: a loom
        over its hours on one Tuesday and under them on the Wednesday
        is not a problem, and reporting it as one buries the week that
        really is full.
        """
        from apps.manufacturing.orders import WorkCentre

        from .capacity import LoadBook, load_profile

        run = self.get_object()
        book = LoadBook(run.warehouse, run.planned_on, run.horizon_end)
        centres = WorkCentre.objects.filter(is_active=True)
        return Response([
            {
                "work_centre": row["work_centre"].pk,
                "code": row["work_centre"].code,
                "week_beginning": row["week_beginning"],
                "available_minutes": row["available_minutes"],
                "booked_minutes": row["booked_minutes"],
                "spare_minutes": row["spare_minutes"],
                "utilisation_percent": row["utilisation_percent"],
                "unscheduled_minutes": row["unscheduled_minutes"],
            }
            for row in load_profile(
                book, centres, run.planned_on, run.horizon_end
            )
        ])

    @action(detail=True, methods=["get"])
    def actions(self, request, pk=None):
        """What this run says to move, pull in first."""
        run = self.get_object()
        return Response(
            PlanningActionSerializer(
                run.actions.select_related("item", "warehouse"), many=True
            ).data
        )

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


class PlanningActionViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """
    Orders that exist and are dated wrong. Read-only: an action is a
    message, and the person who owns that order decides.
    """

    queryset = PlanningAction.objects.select_related(
        "run", "item", "warehouse", "work_order", "purchase_order_line",
        "requisition_line",
    )
    serializer_class = PlanningActionSerializer


class PlannedDemandViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = PlannedDemand.objects.select_related(
        "planned_order", "sales_order_line", "work_order", "parent"
    )
    serializer_class = PlannedDemandSerializer


class PromiseViewSet(viewsets.ViewSet):
    """
    What a rep can say on the telephone.

    Read-only and stateless: a quotation reserves nothing, books no
    machine and writes nothing down. Two enquiries the same morning
    get the same date, and the cure is to confirm the order — which
    does reserve stock — not to have the enquiry pretend it did.
    """

    queryset = PlannedOrder.objects.none()

    def _asked(self, request):
        from apps.inventory.models import Item, Warehouse

        item = Item.objects.filter(pk=request.query_params.get("item")).first()
        warehouse = Warehouse.objects.filter(
            pk=request.query_params.get("warehouse")
        ).first()
        if item is None or warehouse is None:
            raise DRFValidationError(["Name an item and a warehouse."])
        on_date = to_date(request.query_params.get("on")) or None
        return item, warehouse, on_date

    def list(self, request):
        """The uncommitted ladder, period by period."""
        from .promise import available_to_promise

        item, warehouse, on_date = self._asked(request)
        return Response(_run(
            available_to_promise, item, warehouse, planned_on=on_date
        ))

    @action(detail=False, methods=["get"])
    def when(self, request):
        """When a quantity could be promised, from stock or from a run."""
        from .promise import capable_to_promise, when_can_we_promise

        item, warehouse, on_date = self._asked(request)
        try:
            quantity = Decimal(str(request.query_params.get("quantity")))
        except (TypeError, InvalidOperation, ValueError):
            raise DRFValidationError(["Say how much, as a number."])
        answer = _run(
            capable_to_promise, item, warehouse, quantity, planned_on=on_date
        )
        return Response({
            "item": answer["item"].pk,
            "sku": answer["item"].sku,
            "quantity": answer["quantity"],
            "date": answer["date"],
            "source": answer["source"],
            "note": answer["note"],
            "from_stock": _run(
                when_can_we_promise, item, warehouse, quantity,
                planned_on=on_date,
            ),
            "bottleneck": (
                answer["bottleneck"].pk
                if answer.get("bottleneck") is not None else None
            ),
        })


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
