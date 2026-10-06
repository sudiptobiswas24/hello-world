from rest_framework import serializers

from .forecast import Forecast
from .mps import MasterScheduleEntry
from .models import (
    PlannedDemand,
    PlannedOrder,
    PlanningAction,
    PlanningRun,
    PlanningSettings,
    TransferRoute,
)


class TransferRouteSerializer(serializers.ModelSerializer):
    from_name = serializers.CharField(source="from_warehouse.name", read_only=True)
    to_name = serializers.CharField(source="to_warehouse.name", read_only=True)

    class Meta:
        model = TransferRoute
        fields = ["id", "from_warehouse", "to_warehouse", "from_name", "to_name", "lead_days",
                  "priority", "is_active", "notes"]


class ForecastSerializer(serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True, default="")

    def get_item_label(self, row):
        return f"{row.item.sku} · {row.item.name}"

    consumed = serializers.SerializerMethodField()
    unconsumed = serializers.SerializerMethodField()

    class Meta:
        model = Forecast
        fields = ["id", "item", "warehouse", "starts_on", "ends_on",
                  "quantity", "is_active", "notes", "consumed", "unconsumed", "item_label", "warehouse_name"]

    def get_consumed(self, obj):
        return obj.consumed()

    def get_unconsumed(self, obj):
        return obj.unconsumed()


class PlanningSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningSettings
        fields = ["id", "horizon_days", "default_buy_lead_days",
                  "default_make_lead_days", "queue_days",
                  "planning_fence_days", "demand_fence_days",
                  "reschedule_tolerance_days", "working_days",
                  "holiday_region", "requisition_requester"]


class PlannedDemandSerializer(serializers.ModelSerializer):
    describes = serializers.CharField(source="describe", read_only=True)

    class Meta:
        model = PlannedDemand
        fields = ["id", "planned_order", "source", "quantity", "needed_by",
                  "sales_order_line", "work_order", "parent", "forecast",
                  "line_number", "describes"]


class PlannedOrderSerializer(serializers.ModelSerializer):
    # Named, so the planner's list reads without a call per row.
    item_sku = serializers.CharField(source="item.sku", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    work_order_number = serializers.CharField(source="work_order.number", read_only=True, default="")
    demands = PlannedDemandSerializer(many=True, read_only=True)
    is_late = serializers.BooleanField(read_only=True)
    days_late = serializers.IntegerField(read_only=True)
    why_late = serializers.CharField(read_only=True)
    days_behind = serializers.IntegerField(read_only=True)
    explanation = serializers.CharField(read_only=True)

    class Meta:
        model = PlannedOrder
        fields = ["id", "run", "item", "item_sku", "item_name", "vendor_name", "work_order_number",
                  "warehouse", "kind", "quantity",
                  "needed_by", "release_on", "lead_days", "level", "bom",
                  "vendor", "from_warehouse", "transfer", "bottleneck",
                  "is_overloaded", "stand_in_note",
                  "rounded_up_by", "fenced_from",
                  "status", "work_order", "requisition_line", "firmed_at",
                  "is_late", "days_late", "why_late", "explanation", "demands",
                  "expected_on", "can_start_on", "held_up_by", "days_behind", "waits_for",
                  "routing"]
        read_only_fields = ["status", "work_order", "requisition_line",
                            "transfer", "firmed_at", "bottleneck",
                            "is_overloaded", "expected_on", "can_start_on", "held_up_by",
                            "waits_for", "routing"]


class PlanningActionSerializer(serializers.ModelSerializer):
    item_sku = serializers.CharField(source="item.sku", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    sentence = serializers.CharField(read_only=True)

    class Meta:
        model = PlanningAction
        fields = ["id", "run", "item", "item_sku", "item_name", "warehouse", "action", "source",
                  "quantity", "scheduled_on", "wanted_on", "days",
                  "work_order", "purchase_order_line", "requisition_line",
                  "because", "inside_fence", "sentence"]


class PlanningRunSerializer(serializers.ModelSerializer):
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    is_complete = serializers.BooleanField(read_only=True)
    late = serializers.SerializerMethodField()
    lapsed = serializers.SerializerMethodField()
    expedites = serializers.SerializerMethodField()
    defers = serializers.SerializerMethodField()
    cancels = serializers.SerializerMethodField()
    overloaded = serializers.SerializerMethodField()

    class Meta:
        model = PlanningRun
        fields = ["id", "warehouse", "warehouse_code", "planned_on", "horizon_end", "ran_at",
                  "cut_links", "deferred_demand", "fence_ends", "unforecast",
                  "notes", "is_complete",
                  "late", "lapsed", "expedites", "defers", "cancels",
                  "overloaded"]
        read_only_fields = ["ran_at", "cut_links", "deferred_demand",
                            "fence_ends", "unforecast"]

    def get_late(self, run):
        """What needed starting before the plan was even run."""
        return [order.pk for order in run.late()]

    def get_lapsed(self, run):
        """What was firmed into a document somebody has since cancelled."""
        return [order.pk for order in run.lapsed()]

    # Counted by the viewset's query where it asked; one run read on its
    # own still counts for itself.
    def get_expedites(self, run):
        return run.expedite_count if hasattr(run, "expedite_count") else run.expedites().count()

    def get_defers(self, run):
        return run.defer_count if hasattr(run, "defer_count") else run.defers().count()

    def get_cancels(self, run):
        return run.cancel_count if hasattr(run, "cancel_count") else run.cancels().count()

    def get_overloaded(self, run):
        return [order.pk for order in run.orders.all() if order.is_overloaded]


class MasterScheduleEntrySerializer(serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True, default="")

    def get_item_label(self, row):
        return f"{row.item.sku} · {row.item.name}"

    class Meta:
        model = MasterScheduleEntry
        fields = ["id", "item", "warehouse", "week_of", "quantity", "reason", "work_order",
                  "committed_at", "overload_accepted", "withdrawn_at", "withdrawn_reason", "item_label", "warehouse_name"]
        read_only_fields = ["work_order", "committed_at", "overload_accepted",
                            "withdrawn_at", "withdrawn_reason"]
