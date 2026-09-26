from rest_framework import serializers

from .forecast import Forecast
from .models import (
    PlannedDemand,
    PlannedOrder,
    PlanningAction,
    PlanningRun,
    PlanningSettings,
    TransferRoute,
)


class TransferRouteSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransferRoute
        fields = ["id", "from_warehouse", "to_warehouse", "lead_days",
                  "priority", "is_active", "notes"]


class ForecastSerializer(serializers.ModelSerializer):
    consumed = serializers.SerializerMethodField()
    unconsumed = serializers.SerializerMethodField()

    class Meta:
        model = Forecast
        fields = ["id", "item", "warehouse", "starts_on", "ends_on",
                  "quantity", "is_active", "notes", "consumed", "unconsumed"]

    def get_consumed(self, obj):
        return obj.consumed()

    def get_unconsumed(self, obj):
        return obj.unconsumed()


class PlanningSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningSettings
        fields = ["id", "horizon_days", "default_buy_lead_days",
                  "default_make_lead_days", "queue_days",
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
    demands = PlannedDemandSerializer(many=True, read_only=True)
    is_late = serializers.BooleanField(read_only=True)
    days_late = serializers.IntegerField(read_only=True)
    why_late = serializers.CharField(read_only=True)
    explanation = serializers.CharField(read_only=True)

    class Meta:
        model = PlannedOrder
        fields = ["id", "run", "item", "warehouse", "kind", "quantity",
                  "needed_by", "release_on", "lead_days", "level", "bom",
                  "vendor", "from_warehouse", "transfer", "bottleneck",
                  "is_overloaded", "stand_in_note",
                  "rounded_up_by",
                  "status", "work_order", "requisition_line", "firmed_at",
                  "is_late", "days_late", "why_late", "explanation", "demands"]
        read_only_fields = ["status", "work_order", "requisition_line",
                            "transfer", "firmed_at", "bottleneck",
                            "is_overloaded"]


class PlanningActionSerializer(serializers.ModelSerializer):
    sentence = serializers.CharField(read_only=True)

    class Meta:
        model = PlanningAction
        fields = ["id", "run", "item", "warehouse", "action", "source",
                  "quantity", "scheduled_on", "wanted_on", "days",
                  "work_order", "purchase_order_line", "requisition_line",
                  "because", "sentence"]


class PlanningRunSerializer(serializers.ModelSerializer):
    is_complete = serializers.BooleanField(read_only=True)
    late = serializers.SerializerMethodField()
    lapsed = serializers.SerializerMethodField()
    expedites = serializers.SerializerMethodField()
    defers = serializers.SerializerMethodField()
    cancels = serializers.SerializerMethodField()
    overloaded = serializers.SerializerMethodField()

    class Meta:
        model = PlanningRun
        fields = ["id", "warehouse", "planned_on", "horizon_end", "ran_at",
                  "cut_links", "deferred_demand", "notes", "is_complete",
                  "late", "lapsed", "expedites", "defers", "cancels",
                  "overloaded"]
        read_only_fields = ["ran_at", "cut_links", "deferred_demand"]

    def get_late(self, run):
        """What needed starting before the plan was even run."""
        return [order.pk for order in run.late()]

    def get_lapsed(self, run):
        """What was firmed into a document somebody has since cancelled."""
        return [order.pk for order in run.lapsed()]

    def get_expedites(self, run):
        return run.expedites().count()

    def get_defers(self, run):
        return run.defers().count()

    def get_cancels(self, run):
        return run.cancels().count()

    def get_overloaded(self, run):
        return [order.pk for order in run.overloaded()]
