from rest_framework import serializers

from .bom import BillOfMaterials, BomByproduct, BomComponent
from .orders import (
    MaterialIssue,
    MaterialIssueLine,
    ProductionByproduct,
    ProductionEntry,
    WorkCentre,
    WorkOrder,
    WorkOrderComponent,
    WorkOrderOperation,
)
from .routing import Routing, RoutingOperation
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


class BomComponentSerializer(serializers.ModelSerializer):
    gross_quantity = serializers.SerializerMethodField()

    class Meta:
        model = BomComponent
        fields = ["id", "bom", "item", "quantity", "uom", "waste_percent",
                  "line_number", "notes", "gross_quantity"]

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
                  "is_computed", "is_default", "is_active", "notes",
                  "components", "byproducts"]
        read_only_fields = ["is_computed"]


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
                  "capacity_uom", "available_hours_per_day", "days_per_week",
                  "is_active"]


class WorkOrderOperationSerializer(serializers.ModelSerializer):
    planned_hours = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderOperation
        fields = ["id", "work_order", "sequence", "name", "work_centre",
                  "setup_minutes", "units_per_hour", "planned_minutes",
                  "planned_hours"]

    def get_planned_hours(self, obj):
        return round(obj.planned_minutes / 60, 2)


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
    quantity_produced = serializers.SerializerMethodField()
    wip_balance = serializers.SerializerMethodField()
    unaccounted = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = ["id", "number", "item", "bom", "quantity_ordered", "uom",
                  "warehouse", "work_centre", "scheduled_start", "scheduled_end",
                  "status", "over_production_percent", "planned_unit_cost",
                  "planned_material_cost", "released_at", "closed_at",
                  "close_entry", "reopened_entry", "routing", "notes",
                  "components", "operations", "planned_minutes",
                  "quantity_produced", "wip_balance", "unaccounted"]
        read_only_fields = ["number", "status", "planned_unit_cost",
                            "planned_material_cost", "released_at", "closed_at",
                            "close_entry", "reopened_entry", "routing"]

    def get_planned_minutes(self, obj):
        return round(obj.planned_minutes(), 2)

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
