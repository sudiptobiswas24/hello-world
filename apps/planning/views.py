"""
The planning API.

`runs/plan/` is the one that matters: it is a POST because running the
plan writes a run, and a planner who could refresh a page into a
hundred stored plans would rightly stop using it.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Prefetch, Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.api import whole_number
from apps.core.audit import AuditableViewSetMixin
from apps.core.models import Party, to_date
from apps.inventory.models import Warehouse

from .levels import low_level_codes
from .forecast import Forecast, coverage
from .models import (
    PlannedDemand,
    RescheduleAction,
    PlannedOrder,
    PlanningAction,
    PlanningRun,
    PlanningSettings,
    TransferRoute,
)
from .mps import MasterScheduleEntry, schedule_view
from .mrp import plan
from .serializers import (
    MasterScheduleEntrySerializer,
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
    filter_fields = ["from_warehouse", "to_warehouse", "is_active"]


class ForecastViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Forecast.objects.select_related("item", "warehouse")
    serializer_class = ForecastSerializer
    filter_fields = ["item", "warehouse", "is_active"]
    search_fields = ["item__sku", "item__name"]
    date_field = "starts_on"
    ordering_fields = ["starts_on"]
    action_permission_map = {"accept": "planning.add_forecast"}

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

    def _statistical_args(self, params):
        from apps.inventory.models import Item

        item = Item.objects.filter(pk=params.get("item")).first()
        warehouse = Warehouse.objects.filter(pk=params.get("warehouse")).first()
        if item is None or warehouse is None:
            raise DRFValidationError(["Name an item and a warehouse."])
        try:
            months = int(params.get("months", 6))
        except (TypeError, ValueError):
            raise DRFValidationError(["months is a whole number."])
        trend = str(params.get("trend", "")).lower() in ("1", "true", "yes")
        return item, warehouse, to_date(params.get("on")) or None, months, trend

    @staticmethod
    def _rows(rows):
        return [{"starts_on": row["starts_on"], "ends_on": row["ends_on"],
                 "quantity": str(row["quantity"])} for row in rows]

    @action(detail=False, methods=["get"])
    def propose(self, request):
        """What shipments by season say the coming months will ship. Writes nothing."""
        from .statistical import propose

        found = _run(propose, *self._statistical_args(request.query_params))
        backtest = {key: (str(value) if isinstance(value, Decimal) else value)
                    for key, value in found["backtest"].items()}
        return Response({
            "method": found["method"],
            "history_months": found["history_months"],
            "history_from": found["history_from"],
            "level": str(found["level"]),
            "indices": ({month: str(index) for month, index in found["indices"].items()}
                        if found["indices"] else None),
            "year_on_year_growth": (str(found["year_on_year_growth"])
                                    if found["year_on_year_growth"] is not None else None),
            "trend_applied": found["trend_applied"],
            "backtest": backtest,
            "rows": self._rows(found["rows"]),
            "notes": found["notes"],
        })

    @action(detail=False, methods=["post"])
    def accept(self, request):
        """Work the proposal out again and keep it as forecasts."""
        from .statistical import accept

        created, skipped = _run(accept, *self._statistical_args(request.data))
        return Response({
            "created": ForecastSerializer(created, many=True).data,
            "skipped": [row | {"quantity": str(row["quantity"]), "reason": reason}
                        for row, reason in skipped],
        }, status=201 if created else 200)


class PlanningSettingsViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PlanningSettings.objects.select_related("requisition_requester")
    serializer_class = PlanningSettingsSerializer


class PlanningRunViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    filter_fields = ["warehouse"]
    date_field = "planned_on"
    ordering_fields = ["planned_on", "ran_at"]

    # What each run's summary reads, read once for the page: firmed
    # orders look at the document they became, and the counts of what to
    # move are asked of the database rather than three times a run.
    queryset = PlanningRun.objects.select_related("warehouse").prefetch_related(
        Prefetch("orders", queryset=PlannedOrder.objects.select_related(
            "work_order", "transfer", "requisition_line__requisition")),
    ).annotate(
        expedite_count=Count("actions", filter=Q(actions__action=RescheduleAction.EXPEDITE), distinct=True),
        defer_count=Count("actions", filter=Q(actions__action=RescheduleAction.DEFER), distinct=True),
        cancel_count=Count("actions", filter=Q(actions__action=RescheduleAction.CANCEL), distinct=True),
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
        run = _run(
            plan, warehouse,
            planned_on=request.data.get("planned_on") or None,
            horizon_days=whole_number(request.data, "horizon_days", least=1),
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
    def late(self, request, pk=None):
        """What cannot be ready when it is wanted, by how much, why, and who waits."""
        from .mrp import late_orders

        run = self.get_object()
        return Response([{
            "planned_order": row["order"].pk, "item": row["order"].item.sku,
            "needed_by": str(row["order"].needed_by),
            "expected_on": str(row["order"].expected_on),
            "days_behind": row["days_behind"], "why": row["why"],
            "waiting": [{**waiting, "wanted_on": str(waiting["wanted_on"])}
                        for waiting in row["waiting"]],
        } for row in late_orders(run)])

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
    search_fields = ["item__sku", "item__name"]
    filter_fields = ["run", "status", "kind", "warehouse", "item"]
    date_field = "needed_by"
    ordering_fields = ["needed_by", "release_on", "level"]

    queryset = PlannedOrder.objects.select_related(
        "run", "item__uom", "warehouse", "vendor", "bom", "work_order", "bottleneck",
        "requisition_line",
    ).prefetch_related(Prefetch("demands", queryset=PlannedDemand.objects.select_related(
        # What explanation() names: the order, run or forecast each need is for.
        "parent__item__uom", "sales_order_line__order", "work_order__item", "forecast")))
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

    search_fields = ["item__sku", "item__name"]
    filter_fields = ["run", "action", "item", "warehouse"]
    ordering_fields = ["scheduled_on", "wanted_on"]

    queryset = PlanningAction.objects.select_related(
        "run", "item", "warehouse", "work_order", "purchase_order_line",
        "requisition_line",
    )
    serializer_class = PlanningActionSerializer


class PlannedDemandViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    filter_fields = ["planned_order", "source", "sales_order_line", "work_order", "forecast"]
    search_fields = ["planned_order__item__sku", "planned_order__item__name"]
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


def _text(value):
    return format(Decimal(value).normalize(), "f")


class MasterScheduleViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Build-ahead commitments by week: draft, rough-cut, commit, withdraw."""

    queryset = MasterScheduleEntry.objects.select_related("item", "warehouse", "work_order")
    serializer_class = MasterScheduleEntrySerializer
    filter_fields = ["item", "warehouse"]
    search_fields = ["item__sku", "item__name", "reason"]
    date_field = "week_of"
    ordering_fields = ["week_of"]
    action_permission_map = {"commit": "planning.change_masterscheduleentry",
                             "withdraw": "planning.change_masterscheduleentry"}

    def perform_create(self, serializer):
        _run(serializer.save)

    def perform_update(self, serializer):
        _run(serializer.save)

    def perform_destroy(self, instance):
        _run(instance.delete)

    @action(detail=True, methods=["get"], url_path="rough-cut")
    def rough_cut(self, request, pk=None):
        entry = self.get_object()
        rows = _run(entry.rough_cut, request.query_params.get("on"))
        return Response([{"work_centre": row["work_centre"].code,
                          "needed_minutes": _text(round(row["needed"], 2)),
                          "free_minutes": _text(round(row["free"], 2))} for row in rows])

    @action(detail=True, methods=["post"])
    def commit(self, request, pk=None):
        entry = self.get_object()
        _run(entry.commit, request.data.get("accept_overload", ""), request.data.get("on"))
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        entry = self.get_object()
        _run(entry.withdraw, request.data.get("reason", ""))
        return Response(self.get_serializer(entry).data)

    @action(detail=False, methods=["get"])
    def weeks(self, request):
        """?item=&warehouse=&start=&weeks=8: wanted, coming, scheduled, projected."""
        from django.shortcuts import get_object_or_404

        from apps.inventory.models import Item

        params = request.query_params
        item = get_object_or_404(Item, pk=params.get("item"))
        warehouse = get_object_or_404(Warehouse, pk=params.get("warehouse"))
        try:
            weeks = int(params.get("weeks", 8))
        except ValueError:
            raise DRFValidationError(["weeks is a number."])
        if not params.get("start"):
            raise DRFValidationError(["Give the week to start from."])
        rows = schedule_view(item, warehouse, params.get("start"), weeks, params.get("on"))
        return Response([{**{key: _text(row[key]) for key in (
            "wanted", "coming", "of_which_scheduled", "projected")},
            "week_of": str(row["week_of"])} for row in rows])
