from rest_framework import serializers

from .bom import BillOfMaterials, BomByproduct, BomComponent
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
from .bom import BomSubstitute
from .maintenance import MaintenanceJob, MaintenanceSchedule
from .rolls import FabricRoll
from .tooling import PrintDesign, Tool, ToolUsage
from .routing import Routing, RoutingOperation
from .shifts import Downtime, DowntimeReason, Shift
from .woven import BagSpecification, FabricSpecification, TapeSpecification


class TapeSpecificationSerializer(serializers.ModelSerializer):
    virgin_percent = serializers.SerializerMethodField()
    metres_per_kg = serializers.SerializerMethodField()

    class Meta:
        model = TapeSpecification
        fields = [
            "id", "code", "name", "tape_item", "denier", "tape_width_mm",
            "draw_ratio", "virgin_granule", "regrind_item", "regrind_percent",
            "filler_item", "filler_percent", "masterbatch_item",
            "masterbatch_percent", "uv_item", "uv_percent",
            "extrusion_waste_percent", "waste_recovered_percent",
            "bom", "is_active", "virgin_percent", "metres_per_kg",
        ]
        read_only_fields = ["bom"]

    def get_virgin_percent(self, obj):
        return obj.virgin_percent()

    def get_metres_per_kg(self, obj):
        return round(obj.metres_per_kg(), 2)


class FabricSpecificationSerializer(serializers.ModelSerializer):
    gsm = serializers.SerializerMethodField()
    gsm_deviation_percent = serializers.SerializerMethodField()
    grams_per_metre = serializers.SerializerMethodField()
    metres_per_kg = serializers.SerializerMethodField()

    class Meta:
        model = FabricSpecification
        fields = [
            "id", "code", "name", "fabric_item", "warp_tape", "weft_tape",
            "ends_per_inch", "picks_per_inch", "lay_flat_width_cm", "weave",
            "target_gsm", "gsm_tolerance_percent", "weaving_waste_percent",
            "waste_recovered_percent", "loom_waste_item", "bom", "is_active",
            "gsm", "gsm_deviation_percent", "grams_per_metre", "metres_per_kg",
        ]
        read_only_fields = ["bom"]

    def get_gsm(self, obj):
        return round(obj.gsm(), 3)

    def get_gsm_deviation_percent(self, obj):
        return round(obj.gsm_deviation_percent(), 3)

    def get_grams_per_metre(self, obj):
        return round(obj.grams_per_metre(), 3)

    def get_metres_per_kg(self, obj):
        return round(obj.metres_per_kg(), 4)


class BagSpecificationSerializer(serializers.ModelSerializer):
    cut_length_cm = serializers.SerializerMethodField()
    fabric_area_sqm = serializers.SerializerMethodField()
    fabric_grams = serializers.SerializerMethodField()
    bag_grams = serializers.SerializerMethodField()
    fabric_metres_per_bag = serializers.SerializerMethodField()

    class Meta:
        model = BagSpecification
        fields = [
            "id", "code", "name", "bag_item", "fabric", "bag_width_cm",
            "bag_length_cm", "bottom_hem_cm", "top_hem_cm", "is_laminated",
            "lamination_gsm", "lamination_item", "lamination_waste_percent",
            "print_colours", "printed_faces", "ink_grams_per_sqm_per_colour",
            "ink_item", "thread_grams_per_bag", "thread_item", "liner_item",
            "liner_grams_per_bag", "conversion_waste_percent",
            "waste_recovered_percent", "cutting_waste_item", "bom", "is_active",
            "cut_length_cm", "fabric_area_sqm", "fabric_grams", "bag_grams",
            "fabric_metres_per_bag",
        ]
        read_only_fields = ["bom"]

    def get_cut_length_cm(self, obj):
        return obj.cut_length_cm()

    def get_fabric_area_sqm(self, obj):
        return round(obj.fabric_area_sqm(), 6)

    def get_fabric_grams(self, obj):
        return round(obj.fabric_grams(), 4)

    def get_bag_grams(self, obj):
        return round(obj.bag_grams(), 4)

    def get_fabric_metres_per_bag(self, obj):
        return round(obj.fabric_metres_per_bag(), 4)


class BomSubstituteSerializer(serializers.ModelSerializer):
    class Meta:
        model = BomSubstitute
        fields = ["id", "component", "item", "quantity_per", "priority",
                  "is_active", "notes"]


class BomComponentSerializer(serializers.ModelSerializer):
    gross_quantity = serializers.SerializerMethodField()
    substitutes = BomSubstituteSerializer(many=True, read_only=True)

    class Meta:
        model = BomComponent
        fields = ["id", "bom", "item", "quantity", "uom", "waste_percent",
                  "line_number", "notes", "gross_quantity", "substitutes"]

    def get_gross_quantity(self, obj):
        return round(obj.gross_quantity(), 6)


class BomByproductSerializer(serializers.ModelSerializer):
    class Meta:
        model = BomByproduct
        fields = ["id", "bom", "item", "quantity", "uom", "valuation",
                  "cost_share_percent", "line_number"]


class BillOfMaterialsSerializer(serializers.ModelSerializer):
    components = BomComponentSerializer(many=True, read_only=True)
    byproducts = BomByproductSerializer(many=True, read_only=True)

    class Meta:
        model = BillOfMaterials
        fields = ["id", "item", "version", "name", "quantity_produced", "uom",
                  "is_computed", "is_default", "is_active", "is_rework",
                  "backflush", "notes", "components", "byproducts"]
        read_only_fields = ["is_computed"]


class MaintenanceScheduleSerializer(serializers.ModelSerializer):
    hours_remaining = serializers.SerializerMethodField()
    due_on = serializers.SerializerMethodField()
    is_due = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceSchedule
        fields = ["id", "work_centre", "name", "every_days",
                  "every_run_hours", "duration_minutes", "last_done_on",
                  "is_active", "notes", "hours_remaining", "due_on", "is_due"]

    def get_hours_remaining(self, obj):
        return obj.hours_remaining()

    def get_due_on(self, obj):
        return obj.due_on()

    def get_is_due(self, obj):
        return obj.is_due()


class MaintenanceJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaintenanceJob
        fields = ["id", "schedule", "work_centre", "due_on",
                  "planned_minutes", "done_on", "actual_minutes", "downtime",
                  "notes"]
        read_only_fields = ["done_on", "actual_minutes", "downtime"]


class PrintDesignSerializer(serializers.ModelSerializer):
    is_approved = serializers.BooleanField(read_only=True)
    cylinders_short_by = serializers.SerializerMethodField()

    class Meta:
        model = PrintDesign
        fields = ["id", "code", "name", "customer", "colours",
                  "artwork_reference", "approved_on", "approved_by",
                  "is_active", "notes", "is_approved", "cylinders_short_by"]

    def get_cylinders_short_by(self, obj):
        return obj.cylinder_set()["short_by"]


class ToolSerializer(serializers.ModelSerializer):
    used = serializers.SerializerMethodField()
    remaining = serializers.SerializerMethodField()
    used_percent = serializers.SerializerMethodField()
    is_worn = serializers.SerializerMethodField()

    class Meta:
        model = Tool
        fields = ["id", "code", "name", "kind", "design", "work_centre",
                  "life_limit", "life_uom", "status", "acquired_on", "notes",
                  "used", "remaining", "used_percent", "is_worn"]

    def get_used(self, obj):
        return obj.used()

    def get_remaining(self, obj):
        return obj.remaining()

    def get_used_percent(self, obj):
        return obj.used_percent()

    def get_is_worn(self, obj):
        return obj.is_worn()


class ToolUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolUsage
        fields = ["id", "tool", "entry", "quantity"]


class FabricRollSerializer(serializers.ModelSerializer):
    implied_gsm = serializers.SerializerMethodField()
    metres_per_kg = serializers.SerializerMethodField()
    gsm_deviation_percent = serializers.SerializerMethodField()
    within_tolerance = serializers.SerializerMethodField()

    class Meta:
        model = FabricRoll
        fields = ["id", "lot", "specification", "entry", "width_mm",
                  "length_m", "net_weight_kg", "core_weight_kg", "is_tubular",
                  "notes", "implied_gsm", "metres_per_kg",
                  "gsm_deviation_percent", "within_tolerance"]

    def get_implied_gsm(self, obj):
        return obj.implied_gsm()

    def get_metres_per_kg(self, obj):
        return obj.metres_per_kg()

    def get_gsm_deviation_percent(self, obj):
        deviation = obj.gsm_deviation_percent()
        return None if deviation is None else round(deviation, 3)

    def get_within_tolerance(self, obj):
        return obj.is_within_tolerance()


class RoutingOperationSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoutingOperation
        fields = ["id", "routing", "sequence", "name", "work_centre",
                  "setup_minutes", "units_per_hour", "rate_uom", "notes"]


class RoutingSerializer(serializers.ModelSerializer):
    operations = RoutingOperationSerializer(many=True, read_only=True)

    class Meta:
        model = Routing
        fields = ["id", "code", "name", "description", "is_active", "operations"]


class WorkCentreSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkCentre
        fields = ["id", "code", "name", "description", "capacity_per_hour",
                  "capacity_uom", "available_hours_per_day", "working_days",
                  "holiday_region",
                  "machine_rate_per_hour", "labour_rate_per_hour",
                  "overhead_rate_per_hour", "conversion_rate_per_hour",
                  "is_active"]

    conversion_rate_per_hour = serializers.SerializerMethodField()

    def get_conversion_rate_per_hour(self, obj):
        return obj.conversion_rate_per_hour()


class WorkOrderOperationSerializer(serializers.ModelSerializer):
    planned_hours = serializers.SerializerMethodField()

    minutes_booked = serializers.SerializerMethodField()
    quantity_completed = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderOperation
        fields = ["id", "work_order", "sequence", "name", "work_centre",
                  "setup_minutes", "units_per_hour", "planned_minutes",
                  "planned_hours", "minutes_booked", "quantity_completed"]

    def get_planned_hours(self, obj):
        return round(obj.planned_minutes / 60, 2)

    def get_minutes_booked(self, obj):
        return obj.minutes_booked()

    def get_quantity_completed(self, obj):
        return obj.quantity_completed()


class WorkOrderComponentSerializer(serializers.ModelSerializer):
    quantity_issued = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderComponent
        fields = ["id", "work_order", "item", "quantity_required", "uom",
                  "waste_percent", "line_number", "quantity_issued"]

    def get_quantity_issued(self, obj):
        return round(obj.quantity_issued(), 4)


class WorkOrderSerializer(serializers.ModelSerializer):
    components = WorkOrderComponentSerializer(many=True, read_only=True)
    operations = WorkOrderOperationSerializer(many=True, read_only=True)
    planned_minutes = serializers.SerializerMethodField()
    minutes_booked = serializers.SerializerMethodField()
    conversion_cost = serializers.SerializerMethodField()
    conversion_variance = serializers.SerializerMethodField()
    quantity_produced = serializers.SerializerMethodField()
    wip_balance = serializers.SerializerMethodField()
    unaccounted = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = ["id", "number", "item", "bom", "quantity_ordered", "uom",
                  "warehouse", "work_centre", "scheduled_start", "scheduled_end",
                  "status", "over_production_percent", "planned_unit_cost",
                  "planned_material_cost", "released_at", "closed_at",
                  "close_entry", "reopened_entry", "routing", "sales_order_line",
                  "notes",
                  "time_allowance_percent", "planned_conversion_cost",
                  "backflush", "rework_of",
                  "components", "operations", "planned_minutes",
                  "minutes_booked", "conversion_cost", "conversion_variance",
                  "quantity_produced", "wip_balance", "unaccounted"]
        read_only_fields = ["number", "status", "planned_unit_cost",
                            "planned_material_cost", "released_at", "closed_at",
                            "close_entry", "reopened_entry", "routing",
                            "backflush", "planned_conversion_cost"]

    def get_planned_minutes(self, obj):
        return round(obj.planned_minutes(), 2)

    def get_minutes_booked(self, obj):
        return obj.minutes_booked()

    def get_conversion_cost(self, obj):
        return round(obj.conversion_cost(), 2)

    def get_conversion_variance(self, obj):
        return round(obj.conversion_variance(), 2)

    def get_quantity_produced(self, obj):
        return round(obj.quantity_produced(), 4)

    def get_wip_balance(self, obj):
        return round(obj.wip_balance(), 2)

    def get_unaccounted(self, obj):
        return round(obj.unaccounted(), 2)


class MaterialIssueLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialIssueLine
        fields = ["id", "issue", "item", "quantity", "uom", "lot", "bin",
                  "returns_line", "unit_cost", "stock_movement", "line_number"]
        read_only_fields = ["unit_cost", "stock_movement"]


class MaterialIssueSerializer(serializers.ModelSerializer):
    lines = MaterialIssueLineSerializer(many=True, read_only=True)

    class Meta:
        model = MaterialIssue
        fields = ["id", "number", "work_order", "direction", "issue_date",
                  "warehouse", "memo", "posted", "posted_at", "posted_value",
                  "journal_entry", "voided_entry", "voided_at", "lines"]
        read_only_fields = ["number", "posted", "posted_at", "posted_value",
                            "journal_entry", "voided_entry", "voided_at"]


class ProductionByproductSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionByproduct
        fields = ["id", "entry", "item", "quantity", "uom", "lot", "bin",
                  "unit_value", "stock_movement", "line_number"]
        read_only_fields = ["unit_value", "stock_movement"]


class ProductionEntrySerializer(serializers.ModelSerializer):
    byproducts = ProductionByproductSerializer(many=True, read_only=True)

    class Meta:
        model = ProductionEntry
        fields = ["id", "number", "work_order", "entry_date", "warehouse",
                  "quantity_produced", "quantity_scrapped", "uom", "lot", "bin",
                  "work_centre", "memo", "posted", "posted_at", "posted_value",
                  "unit_cost", "stock_movement", "journal_entry", "voided_entry",
                  "voided_at", "byproducts"]
        read_only_fields = ["number", "posted", "posted_at", "posted_value",
                            "unit_cost", "stock_movement", "journal_entry",
                            "voided_entry", "voided_at"]


class TimeBookingSerializer(serializers.ModelSerializer):
    hours = serializers.SerializerMethodField()

    class Meta:
        model = TimeBooking
        fields = ["id", "number", "work_order", "operation", "booking_date",
                  "started_at", "shift", "operators", "minutes", "hours",
                  "quantity_completed", "memo", "hourly_rate", "posted",
                  "posted_at", "posted_value", "journal_entry", "voided_entry",
                  "voided_at"]
        read_only_fields = ["number", "hourly_rate", "posted", "posted_at",
                            "posted_value", "journal_entry", "voided_entry",
                            "voided_at"]

    def get_hours(self, obj):
        return round(obj.hours(), 3)


class ShiftSerializer(serializers.ModelSerializer):
    ends_at = serializers.SerializerMethodField()
    crosses_midnight = serializers.SerializerMethodField()

    class Meta:
        model = Shift
        fields = ["id", "code", "name", "starts_at", "hours", "ends_at",
                  "crosses_midnight", "is_active"]

    def get_ends_at(self, obj):
        return obj.ends_at()

    def get_crosses_midnight(self, obj):
        return obj.crosses_midnight()


class DowntimeReasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = DowntimeReason
        fields = ["id", "code", "name", "is_planned", "is_active"]


class DowntimeSerializer(serializers.ModelSerializer):
    hours = serializers.SerializerMethodField()

    class Meta:
        model = Downtime
        fields = ["id", "number", "work_centre", "shift_date", "shift",
                  "reason", "minutes", "hours", "work_order", "notes"]
        read_only_fields = ["number"]

    def get_hours(self, obj):
        return round(obj.hours(), 3)
