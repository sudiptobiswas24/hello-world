from decimal import Decimal

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
from .costing import CostVersion, StandardCost
from .changeover import ChangeoverRule, SetupFamily
from .jobwork import JobWorkChallan, JobWorkLine, JobWorkLoss
from .machines import Machine
from .certificates import TestCertificate
from .energy import EnergyMeter, EnergyTariff, MeterReading
from .inward import (
    CustomerMaterialReceipt,
    CustomerMaterialReceiptLine,
    CustomerMaterialReturn,
    CustomerMaterialReturnLine,
)
from .quoting import CostSheet, CostSheetLine, MaterialRate, QuotePolicy, StageRate
from .maintenance import MaintenanceJob, MaintenanceSchedule
from .rolls import FabricRoll
from .tooling import PrintDesign, Tool, ToolUsage
from .liners import FilmSpecification, LinerSpecification
from .manning import CrewAssignment
from .routing import AlternateRouting, Routing, RoutingOperation
from .scrap import OperationReport, ProductionScrap, ScrapReason
from .rebatch import Rebatch, RebatchLine
from .shifts import Downtime, DowntimeReason, Shift
from apps.accounting.models import Tax
from apps.inventory.models import Item
from apps.sales.models import Quotation

from .woven import (
    FOLD_ALLOWANCE_CM, BagSpecification, FabricSpecification, TapeSpecification,
)


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
            "denier_tolerance_percent", "min_tenacity_gpd", "elongation_min_percent",
            "elongation_max_percent",
            "bom", "is_active", "virgin_percent", "metres_per_kg",
            "valid_from", "valid_to", "routing",
        ]
        read_only_fields = ["bom"]

    def get_virgin_percent(self, obj):
        return obj.virgin_percent()

    def get_metres_per_kg(self, obj):
        return round(obj.metres_per_kg(), 2)


class FilmSpecificationSerializer(serializers.ModelSerializer):
    grams_per_metre = serializers.SerializerMethodField()

    class Meta:
        model = FilmSpecification
        fields = [
            "id", "code", "name", "film_item", "micron", "lay_flat_width_cm",
            "base_polymer", "lldpe_item", "lldpe_percent", "masterbatch_item",
            "masterbatch_percent", "extrusion_waste_percent", "waste_item",
            "waste_recovered_percent", "micron_tolerance_percent", "routing", "valid_from",
            "valid_to", "bom", "is_active", "grams_per_metre",
        ]
        read_only_fields = ["bom"]

    def get_grams_per_metre(self, obj):
        return str(obj.grams_per_metre().quantize(Decimal("0.001")))


class LinerSpecificationSerializer(serializers.ModelSerializer):
    liner_grams = serializers.SerializerMethodField()

    class Meta:
        model = LinerSpecification
        fields = [
            "id", "code", "name", "liner_item", "film", "cut_length_cm", "seal_waste_percent",
            "waste_recovered_percent", "weight_tolerance_percent", "routing", "valid_from",
            "valid_to", "bom", "is_active", "liner_grams",
        ]
        read_only_fields = ["bom"]

    def get_liner_grams(self, obj):
        return str(obj.liner_grams().quantize(Decimal("0.001")))


class FabricSpecificationSerializer(serializers.ModelSerializer):
    gsm = serializers.SerializerMethodField()
    gsm_deviation_percent = serializers.SerializerMethodField()
    grams_per_metre = serializers.SerializerMethodField()
    metres_per_kg = serializers.SerializerMethodField()

    class Meta:
        model = FabricSpecification
        fields = [
            "id", "code", "name", "fabric_item", "warp_tape", "weft_tape",
            "ends_per_inch", "picks_per_inch", "lay_flat_width_cm", "shrink_percent", "weave",
            "is_leno",
            "target_gsm", "gsm_tolerance_percent", "weaving_waste_percent",
            "waste_recovered_percent", "loom_waste_item", "bom", "is_active",
            "gsm", "gsm_deviation_percent", "grams_per_metre", "metres_per_kg",
            "warp_strength_min_n", "weft_strength_min_n", "mesh_tolerance_per_inch",
            "valid_from", "valid_to", "routing",
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


class FoldAllowanceDefault:
    """
    A fold named without an allowance takes the fold's own: 1.25 inches
    single, 2 double, 0.75 easy-open without fold. Given one, it stands.
    """

    def validate(self, attrs):
        attrs = super().validate(attrs)
        fold = attrs.get("fold_type")
        if fold and "bottom_hem_cm" not in self.initial_data:
            attrs["bottom_hem_cm"] = FOLD_ALLOWANCE_CM[fold]
        return attrs


class CoatingLineSerializer(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=Item.objects.all())
    parts = serializers.DecimalField(max_digits=10, decimal_places=3)


class BagSpecificationSerializer(FoldAllowanceDefault, serializers.ModelSerializer):
    # Written through the specification, never line by line, so that
    # the blend is checked and the bill rebuilt in the same save.
    coating = CoatingLineSerializer(many=True, required=False, source="coating_lines")
    cut_length_cm = serializers.SerializerMethodField()
    fabric_area_sqm = serializers.SerializerMethodField()
    fabric_grams = serializers.SerializerMethodField()
    bag_grams = serializers.SerializerMethodField()
    fabric_metres_per_bag = serializers.SerializerMethodField()
    construction = serializers.SerializerMethodField()

    class Meta:
        model = BagSpecification
        fields = [
            "id", "code", "name", "bag_item", "fabric", "bag_width_cm",
            "bag_length_cm", "bottom_hem_cm", "top_hem_cm", "is_laminated",
            "lamination_gsm", "coating", "lamination_waste_percent",
            "print_colours", "print_colours_back", "ink_grams_per_sqm_per_colour",
            "ink_item", "reducer_item", "reducer_percent", "solvent_item", "solvent_percent",
            "fold_type", "thread_grams_per_bag", "thread_denier", "stitches_per_dm",
            "thread_item", "liner_item", "print_design", "registration_tolerance_mm",
            "max_delta_e", "seconds_item", "seconds_percent",
            "seam_strength_min_n",
            "drop_test_drops", "bond_strength_min_n", "uv_retention_min_percent",
            "uv_exposure_hours",
            "liner_grams_per_bag", "conversion_waste_percent",
            "waste_recovered_percent", "cutting_waste_item", "bom", "is_active",
            "gusset_cm", "closure", "bopp_film_item", "bopp_micron", "bopp_faces",
            "bopp_waste_percent", "valve_patch_item", "valve_patch_grams",
            "cover_patch_item", "cover_patch_grams", "handle_item", "handle_grams",
            "dcut_area_sqcm", "metallic_film_item", "metallic_micron",
            "metallic_coverage_percent", "liner_micron",
            "liner_width_cm", "liner_length_cm", "target_grams", "weight_tolerance_percent",
            "cut_length_cm", "fabric_area_sqm", "fabric_grams", "bag_grams",
            "fabric_metres_per_bag", "construction",
            "valid_from", "valid_to", "lamination_tolerance_percent", "routing",
        ]
        read_only_fields = ["bom"]

    def get_cut_length_cm(self, obj):
        return obj.cut_length_cm()

    def get_construction(self, obj):
        return obj.construction()

    def get_fabric_area_sqm(self, obj):
        return round(obj.fabric_area_sqm(), 6)

    def get_fabric_grams(self, obj):
        return round(obj.fabric_grams(), 4)

    def get_bag_grams(self, obj):
        return round(obj.bag_grams(), 4)

    def get_fabric_metres_per_bag(self, obj):
        return round(obj.fabric_metres_per_bag(), 4)

    def _save(self, instance, validated_data):
        blend = validated_data.pop("coating_lines", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if blend is not None:
            instance.set_coating([(row["item"], row["parts"]) for row in blend])
        instance.save()
        return instance

    def create(self, validated_data):
        return self._save(BagSpecification(), validated_data)

    def update(self, instance, validated_data):
        return self._save(instance, validated_data)


class BagSolveSerializer(FoldAllowanceDefault, serializers.ModelSerializer):
    """
    A sack described before any fabric exists for it: the construction,
    the contracted weight and the loom's mesh, and nothing saved.
    """

    target_grams = serializers.DecimalField(max_digits=10, decimal_places=3,
                                            min_value=Decimal("0.001"))
    ends_per_inch = serializers.DecimalField(max_digits=6, decimal_places=2,
                                             min_value=Decimal("0.01"))
    picks_per_inch = serializers.DecimalField(max_digits=6, decimal_places=2,
                                              min_value=Decimal("0.01"))
    shrink_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=Decimal("0"), max_value=Decimal("99.99"),
        default=FabricSpecification._meta.get_field("shrink_percent").default,
    )
    warp_tape_denier = serializers.DecimalField(max_digits=8, decimal_places=2,
                                                min_value=Decimal("0.01"), required=False)

    class Meta:
        model = BagSpecification
        fields = [
            "bag_width_cm", "bag_length_cm", "gusset_cm", "bottom_hem_cm", "top_hem_cm",
            "is_laminated", "lamination_gsm", "bopp_micron", "bopp_faces",
            "print_colours", "print_colours_back", "ink_grams_per_sqm_per_colour",
            "fold_type", "thread_grams_per_bag", "thread_denier", "stitches_per_dm",
            "liner_grams_per_bag", "liner_micron",
            "liner_width_cm", "liner_length_cm", "valve_patch_grams", "cover_patch_grams",
            "handle_grams", "dcut_area_sqcm", "metallic_micron", "metallic_coverage_percent",
            "weight_tolerance_percent", "target_grams", "ends_per_inch", "picks_per_inch",
            "shrink_percent", "warp_tape_denier",
        ]


class TestCertificateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestCertificate
        fields = ["id", "number", "delivery", "issued_on", "content", "voided_at",
                  "voided_reason"]
        read_only_fields = fields


class MaterialRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialRate
        fields = ["id", "item", "rate", "valid_from", "note"]


class CustomerMaterialReceiptLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerMaterialReceiptLine
        fields = ["id", "receipt", "item", "lot", "quantity", "declared_value"]


class CustomerMaterialReceiptSerializer(serializers.ModelSerializer):
    lines = CustomerMaterialReceiptLineSerializer(many=True, read_only=True)

    class Meta:
        model = CustomerMaterialReceipt
        fields = ["id", "number", "customer", "warehouse", "received_on", "their_challan",
                  "their_challan_date", "posted", "posted_at", "voided_at", "voided_reason",
                  "lines"]
        read_only_fields = ["number", "posted", "posted_at", "voided_at", "voided_reason"]
        validators = []


class CustomerMaterialReturnLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerMaterialReturnLine
        fields = ["id", "material_return", "receipt_line", "item", "lot", "quantity"]


class CustomerMaterialReturnSerializer(serializers.ModelSerializer):
    lines = CustomerMaterialReturnLineSerializer(many=True, read_only=True)

    class Meta:
        model = CustomerMaterialReturn
        fields = ["id", "number", "customer", "warehouse", "returned_on", "posted",
                  "posted_at", "voided_at", "voided_reason", "lines"]
        read_only_fields = ["number", "posted", "posted_at", "voided_at", "voided_reason"]


class EnergyMeterSerializer(serializers.ModelSerializer):
    serves = serializers.SerializerMethodField()

    class Meta:
        model = EnergyMeter
        fields = ["id", "code", "machine", "work_centre", "serves", "multiplier", "installed_on",
                  "initial_reading", "retired_on"]

    def get_serves(self, obj):
        return str(obj.serves())
        # The model's own save says why a pair is wrong; the serializer's
        # generic unique-together message would not.
        validators = []


class MeterReadingSerializer(serializers.ModelSerializer):
    meter_code = serializers.CharField(source="meter.code", read_only=True)
    shift_name = serializers.CharField(source="shift.name", read_only=True, default="")
    read_by_name = serializers.CharField(source="read_by.party.name", read_only=True, default="")

    class Meta:
        model = MeterReading
        fields = ["id", "meter", "meter_code", "shift_date", "shift", "shift_name", "reading",
                  "read_by", "read_by_name", "voided_at", "voided_reason"]
        read_only_fields = ["voided_at", "voided_reason"]
        validators = []


class EnergyTariffSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnergyTariff
        fields = ["id", "rate", "valid_from", "note"]


class StageRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StageRate
        fields = ["id", "stage", "rate", "valid_from", "note",
            "work_centre",
        ]


class QuotePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = QuotePolicy
        fields = ["id", "overhead_percent", "margin_percent", "valid_from", "note"]


class CostSheetLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostSheetLine
        fields = ["kind", "stage", "item", "description", "quantity", "rate", "amount",
                  "last_receipt_cost"]


class CostSheetSerializer(serializers.ModelSerializer):
    lines = CostSheetLineSerializer(many=True, read_only=True)
    per_kg = serializers.SerializerMethodField()
    order_value = serializers.SerializerMethodField()

    class Meta:
        model = CostSheet
        fields = [
            "id", "specification", "quantity", "costed_on", "bag_grams", "overhead_percent",
            "margin_percent", "material", "conversion", "credit", "overhead", "cost", "price",
            "quoted_price", "per_kg", "order_value", "quotation_line", "lines",
        ]
        read_only_fields = fields

    def get_per_kg(self, obj):
        return str(obj.per_kg())

    def get_order_value(self, obj):
        return str(obj.order_value())


class CostSheetRequestSerializer(serializers.Serializer):
    specification = serializers.PrimaryKeyRelatedField(queryset=BagSpecification.objects.all())
    quantity = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("1"))
    costed_on = serializers.DateField(required=False)
    margin_percent = serializers.DecimalField(max_digits=6, decimal_places=2, required=False)


class QuoteRequestSerializer(serializers.Serializer):
    quotation = serializers.PrimaryKeyRelatedField(queryset=Quotation.objects.all())
    # Required, if only as an empty list: a line's taxes are its own,
    # and none is a statement, not a default.
    taxes = serializers.PrimaryKeyRelatedField(queryset=Tax.objects.all(), many=True,
                                               allow_empty=True)


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
                  "backflush", "expected_reject_percent", "notes",
                  "components", "byproducts",
            "routing", "valid_from", "valid_to", "is_phantom",
        ]
        read_only_fields = ["is_computed"]


class StandardCostSerializer(serializers.ModelSerializer):
    class Meta:
        model = StandardCost
        fields = ["id", "version", "item", "material", "conversion",
                  "byproduct_credit", "total", "is_rolled", "bom", "notes"]
        read_only_fields = ["total", "is_rolled", "bom"]


class CostVersionSerializer(serializers.ModelSerializer):
    is_published = serializers.BooleanField(read_only=True)
    items = serializers.SerializerMethodField()

    class Meta:
        model = CostVersion
        fields = ["id", "code", "name", "effective_from", "notes",
                  "published_at", "published_on", "revaluation_entry",
                  "is_published", "items"]
        read_only_fields = ["published_at", "published_on",
                            "revaluation_entry"]

    def get_items(self, obj):
        return obj.costs.count()


class MaintenanceScheduleSerializer(serializers.ModelSerializer):
    hours_remaining = serializers.SerializerMethodField()
    due_on = serializers.SerializerMethodField()
    is_due = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceSchedule
        fields = ["id", "work_centre", "machine", "serves", "name", "every_days",
                  "every_run_hours", "duration_minutes", "last_done_on",
                  "is_active", "notes", "hours_remaining", "due_on", "is_due"]

    serves = serializers.SerializerMethodField()

    def get_serves(self, obj):
        return str(obj.machine or obj.work_centre)

    def get_hours_remaining(self, obj):
        return obj.hours_remaining()

    def get_due_on(self, obj):
        return obj.due_on()

    def get_is_due(self, obj):
        return obj.is_due()


class MaintenanceJobSerializer(serializers.ModelSerializer):
    serves = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    schedule_name = serializers.CharField(source="schedule.name", read_only=True, default="")
    technician_name = serializers.CharField(source="technician.party.name", read_only=True, default="")

    class Meta:
        model = MaintenanceJob
        fields = ["id", "schedule", "schedule_name", "work_centre", "machine", "serves", "title",
                  "status", "due_on", "planned_minutes", "done_on", "actual_minutes", "downtime",
                  "notes", "is_breakdown", "fault", "technician", "technician_name", "cause",
                  "action_taken", "cancelled_at", "cancelled_reason", "labour_minutes"]
        read_only_fields = ["done_on", "actual_minutes", "downtime", "is_breakdown", "cause",
                            "action_taken", "cancelled_at", "cancelled_reason"]

    labour_minutes = serializers.SerializerMethodField()

    def get_labour_minutes(self, obj):
        return str(obj.labour_minutes())

    def get_serves(self, obj):
        return str(obj.machine or obj.work_centre)

    def get_title(self, obj):
        # What the job is, in the words it was raised with.
        return (obj.schedule.name if obj.schedule_id else obj.fault) or obj.notes or "Repair"

    def get_status(self, obj):
        return "cancelled" if obj.cancelled_at else "done" if obj.done_on else "open"


class MaintenanceJobDetailSerializer(MaintenanceJobSerializer):
    """One job, with the fitters' time and the spares on it."""

    labour = serializers.SerializerMethodField()
    spares = serializers.SerializerMethodField()
    spares_value = serializers.SerializerMethodField()

    class Meta(MaintenanceJobSerializer.Meta):
        fields = MaintenanceJobSerializer.Meta.fields + ["labour", "spares", "spares_value"]

    def get_labour(self, obj):
        return [{"id": row.pk, "technician_name": row.technician.party.name,
                 "worked_on": row.worked_on, "minutes": str(row.minutes)}
                for row in obj.labour.select_related("technician__party")]

    def get_spares(self, obj):
        rows = []
        for issue in obj.spares.select_related("adjustment").prefetch_related("adjustment__lines__item"):
            rows.append({
                "id": issue.pk, "adjustment": issue.adjustment.number,
                "items": ", ".join(f"{line.item.sku} x {abs(line.quantity).normalize():f}"
                                   for line in issue.adjustment.lines.all()),
                "value": str(issue.value()), "standing": issue.is_standing(),
            })
        return rows

    def get_spares_value(self, obj):
        return str(obj.spares_value())


class PrintDesignSerializer(serializers.ModelSerializer):
    is_approved = serializers.BooleanField(read_only=True)
    cylinders_short_by = serializers.SerializerMethodField()

    class Meta:
        model = PrintDesign
        fields = ["id", "code", "name", "customer", "colours",
                  "artwork_reference", "approved_on", "approved_by", "engraving_lead_days",
                  "is_active", "notes", "is_approved", "cylinders_short_by", "ready_on"]

    ready_on = serializers.SerializerMethodField()

    def get_ready_on(self, obj):
        from django.utils import timezone

        return obj.readiness(timezone.localdate())[0]

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
                  "used", "remaining", "used_percent", "is_worn", "expected_on"]

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
        fields = ["id", "lot", "specification", "entry", "machine", "width_mm",
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
                  "is_outside", "outside_lead_days", "outside_cost_per_unit",
                  "setup_minutes", "units_per_hour", "rate_uom", "notes"]


class CrewAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CrewAssignment
        fields = ["id", "employee", "work_centre", "shift", "valid_from", "valid_to"]


class AlternateRoutingSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlternateRouting
        fields = ["id", "bom", "routing", "priority", "notes"]


class RoutingSerializer(serializers.ModelSerializer):
    operations = RoutingOperationSerializer(many=True, read_only=True)

    class Meta:
        model = Routing
        fields = ["id", "code", "name", "description", "is_active", "operations"]


class SetupFamilySerializer(serializers.ModelSerializer):
    class Meta:
        model = SetupFamily
        fields = ["id", "item", "work_centre", "family"]


class ChangeoverRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangeoverRule
        fields = ["id", "work_centre", "from_family", "to_family", "minutes",
                  "purge_kg", "notes"]


class MachineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Machine
        fields = ["id", "work_centre", "code", "name", "capacity_per_hour",
                  "capacity_uom", "available_hours_per_day", "working_days",
                  "hours_per_day", "days_pattern", "is_active", "notes",
                  "min_width_cm", "max_width_cm", "min_length_cm", "max_length_cm",
                  "max_colours", "inserts_liner",
            "contractor",
        ]

    hours_per_day = serializers.SerializerMethodField()
    days_pattern = serializers.SerializerMethodField()

    def get_hours_per_day(self, obj):
        """What it falls back to, shown beside what it states."""
        return obj.hours_per_day()

    def get_days_pattern(self, obj):
        return obj.days_pattern()


class WorkCentreSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkCentre
        fields = ["id", "code", "name", "description", "capacity_per_hour",
                  "capacity_uom", "available_hours_per_day", "working_days",
                  "holiday_region",
                  "machine_rate_per_hour", "labour_rate_per_hour",
                  "overhead_rate_per_hour", "conversion_rate_per_hour",
                  "standard_kwh_per_hour", "is_active", "efficiency_percent",
                  "operators_per_machine", "speed_basis", "tape_ends", "line_speed_m_per_min", "loom_rpm",
                  "shuttles"]

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
                  "is_outside", "outside_lead_days", "planned_outside_cost",
                  "machine", "setup_minutes", "units_per_hour", "planned_minutes",
                  "planned_hours", "minutes_booked", "quantity_completed"]

    def get_planned_hours(self, obj):
        return round(obj.planned_minutes / 60, 2)

    def get_minutes_booked(self, obj):
        return obj.minutes_booked()

    def get_quantity_completed(self, obj):
        return obj.quantity_completed()


class WorkOrderComponentSerializer(serializers.ModelSerializer):
    quantity_issued = serializers.SerializerMethodField()
    item_sku = serializers.CharField(source="item.sku", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = WorkOrderComponent
        fields = ["id", "work_order", "item", "item_sku", "item_name", "quantity_required", "uom",
                  "waste_percent", "line_number", "quantity_issued"]

    def get_quantity_issued(self, obj):
        return round(obj.quantity_issued(), 4)


class WorkOrderListSerializer(serializers.ModelSerializer):
    """
    A run as a list shows it: what, how many, where, when, and its state.
    What it booked and cost is for the run's own page; working that out
    for fifty rows was fifty runs' worth of time bookings and ledger.
    """

    item_sku = serializers.CharField(source="item.sku", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    work_centre_code = serializers.CharField(source="work_centre.code", read_only=True, default="")

    class Meta:
        model = WorkOrder
        fields = ["id", "number", "item", "item_sku", "item_name", "quantity_ordered", "uom",
                  "warehouse", "work_centre", "work_centre_code", "scheduled_start", "scheduled_end",
                  "status", "released_at", "closed_at", "sales_order_line"]
        read_only_fields = fields


class WorkOrderSerializer(serializers.ModelSerializer):
    item_sku = serializers.CharField(source="item.sku", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    work_centre_code = serializers.CharField(source="work_centre.code", read_only=True, default="")
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
        fields = ["id", "number", "item", "item_sku", "item_name", "work_centre_code", "bom", "quantity_ordered",
                  "quantity_to_start", "uom",
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
                  "journal_entry", "voided_entry", "voided_at", "lines",
            "contractor",
        ]
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
                  "work_centre", "machine", "memo", "posted", "posted_at", "posted_value",
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
                  "started_at", "shift", "machine", "operators", "minutes", "hours",
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
    serves = serializers.SerializerMethodField()
    reason_name = serializers.CharField(source="reason.name", read_only=True, default="")
    is_planned = serializers.BooleanField(source="reason.is_planned", read_only=True, default=False)
    shift_name = serializers.CharField(source="shift.name", read_only=True, default="")
    work_order_number = serializers.CharField(source="work_order.number", read_only=True, default="")

    class Meta:
        model = Downtime
        fields = ["id", "number", "work_centre", "machine", "serves", "shift_date",
                  "shift", "shift_name", "reason", "reason_name", "is_planned", "minutes", "hours",
                  "work_order", "work_order_number", "notes"]

    def get_serves(self, obj):
        return str(obj.machine or obj.work_centre)
        read_only_fields = ["number"]

    def get_hours(self, obj):
        return round(obj.hours(), 3)


class JobWorkLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobWorkLine
        fields = ["id", "challan", "operation", "description", "hsn_code", "quantity",
                  "value", "tax_rate", "is_capital_goods"]


class JobWorkChallanSerializer(serializers.ModelSerializer):
    lines = JobWorkLineSerializer(many=True, read_only=True)

    class Meta:
        model = JobWorkChallan
        fields = ["id", "number", "job_worker", "challan_date", "vehicle", "notes",
                  "job_worker_gstin", "job_worker_state", "posted", "posted_at",
                  "voided_at", "lines"]
        read_only_fields = ["number", "job_worker_gstin", "job_worker_state", "posted",
                            "posted_at", "voided_at"]


class JobWorkLossSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobWorkLoss
        fields = ["id", "line", "loss_date", "quantity", "note", "voided_at"]


class ScrapReasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapReason
        fields = ["id", "code", "name", "is_active"]


class ProductionScrapSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionScrap
        fields = ["id", "entry", "reason", "operation", "quantity"]


class OperationReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = OperationReport
        fields = ["id", "operation", "reported_on", "quantity_good", "machine", "memo",
                  "voided_at", "voided_reason"]
        read_only_fields = fields


class RebatchLineSerializer(serializers.ModelSerializer):
    lot_code = serializers.CharField(source="lot.code", read_only=True)

    class Meta:
        model = RebatchLine
        fields = ["id", "side", "lot", "lot_code", "quantity"]


class RebatchSerializer(serializers.ModelSerializer):
    lines = RebatchLineSerializer(many=True, read_only=True)

    class Meta:
        model = Rebatch
        fields = ["id", "number", "item", "warehouse", "rebatched_on", "reason", "posted",
                  "posted_at", "voided_at", "voided_reason", "lines"]
        read_only_fields = fields
