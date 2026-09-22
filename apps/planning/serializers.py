from rest_framework import serializers

from .models import PlannedDemand, PlannedOrder, PlanningRun, PlanningSettings


class PlanningSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningSettings
        fields = ["id", "horizon_days", "default_buy_lead_days",
                  "default_make_lead_days", "queue_days",
                  "requisition_requester"]


class PlannedDemandSerializer(serializers.ModelSerializer):
    describes = serializers.CharField(source="describe", read_only=True)

    class Meta:
        model = PlannedDemand
        fields = ["id", "planned_order", "source", "quantity", "needed_by",
                  "sales_order_line", "work_order", "parent", "line_number",
                  "describes"]


class PlannedOrderSerializer(serializers.ModelSerializer):
    demands = PlannedDemandSerializer(many=True, read_only=True)
    is_late = serializers.BooleanField(read_only=True)
    days_late = serializers.IntegerField(read_only=True)
    explanation = serializers.CharField(read_only=True)

    class Meta:
        model = PlannedOrder
        fields = ["id", "run", "item", "warehouse", "kind", "quantity",
                  "needed_by", "release_on", "lead_days", "level", "bom",
                  "vendor", "rounded_up_by", "status", "work_order",
                  "requisition_line", "firmed_at", "is_late", "days_late",
                  "explanation", "demands"]
        read_only_fields = ["status", "work_order", "requisition_line",
                            "firmed_at"]


class PlanningRunSerializer(serializers.ModelSerializer):
    is_complete = serializers.BooleanField(read_only=True)
    late = serializers.SerializerMethodField()
    lapsed = serializers.SerializerMethodField()

    class Meta:
        model = PlanningRun
        fields = ["id", "warehouse", "planned_on", "horizon_end", "ran_at",
                  "cut_links", "deferred_demand", "notes", "is_complete",
                  "late", "lapsed"]
        read_only_fields = ["ran_at", "cut_links", "deferred_demand"]

    def get_late(self, run):
        """What needed starting before the plan was even run."""
        return [order.pk for order in run.late()]

    def get_lapsed(self, run):
        """What was firmed into a document somebody has since cancelled."""
        return [order.pk for order in run.lapsed()]
