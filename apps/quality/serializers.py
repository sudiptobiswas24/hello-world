from rest_framework import serializers

from .calibration import Calibration, Instrument
from .models import (
    Characteristic,
    Inspection,
    InspectionPlan,
    PlanLine,
    Reading,
)
from .release import release_status


class CharacteristicSerializer(serializers.ModelSerializer):
    uom_code = serializers.CharField(source="uom.code", read_only=True, default="")

    class Meta:
        model = Characteristic
        fields = ["id", "code", "name", "kind", "uom", "uom_code", "decimal_places",
                  "is_active", "needs_calibrated_instrument"]


class PlanLineSerializer(serializers.ModelSerializer):
    characteristic_label = serializers.SerializerMethodField()

    class Meta:
        model = PlanLine
        fields = ["id", "plan", "characteristic", "characteristic_label", "target", "lower_limit",
                  "upper_limit", "sample_size", "evaluation", "derived_from",
                  "line_number", "aql", "inspection_level"]

    def get_characteristic_label(self, line):
        return f"{line.characteristic.code} · {line.characteristic.name}"
        read_only_fields = ["derived_from"]


class InspectionPlanSerializer(serializers.ModelSerializer):
    lines = PlanLineSerializer(many=True, read_only=True)
    item_label = serializers.SerializerMethodField()
    lines_count = serializers.SerializerMethodField()

    def get_item_label(self, plan):
        return f"{plan.item.sku} · {plan.item.name}" if plan.item_id else ""

    def get_lines_count(self, plan):
        return len(plan.lines.all())

    class Meta:
        model = InspectionPlan
        fields = ["id", "item", "item_label", "lines_count", "name", "is_mandatory", "is_computed",
                  "is_active", "notes", "lines",
            "valid_from", "valid_to",
        ]
        read_only_fields = ["is_computed"]


class ReadingSerializer(serializers.ModelSerializer):
    characteristic = serializers.SerializerMethodField()

    def get_characteristic(self, reading):
        return reading.plan_line.characteristic.name

    class Meta:
        model = Reading
        fields = ["id", "inspection", "plan_line", "characteristic", "value", "present",
                  "sample_reference", "lower_limit", "upper_limit", "passed",
                  "instrument", "calibration"]
        read_only_fields = ["lower_limit", "upper_limit", "passed", "calibration"]


class InspectionSerializer(serializers.ModelSerializer):
    readings = ReadingSerializer(many=True, read_only=True)
    lot_code = serializers.CharField(source="lot.code", read_only=True)
    item_label = serializers.SerializerMethodField()
    plan_name = serializers.CharField(source="plan.name", read_only=True, default="")
    inspected_by_name = serializers.CharField(source="inspected_by.name", read_only=True, default="")

    def get_item_label(self, inspection):
        return f"{inspection.lot.item.sku} · {inspection.lot.item.name}"
    self_approved = serializers.SerializerMethodField()
    lot_status = serializers.SerializerMethodField()

    class Meta:
        model = Inspection
        fields = ["id", "number", "lot", "plan", "inspected_on", "inspected_by",
                  "disposition", "decided_by", "decision_note", "result",
                  "posted", "posted_at", "voided_at", "voided_reason", "notes",
                  "readings", "self_approved", "lot_status", "lot_size", "sampling",
                  "lot_code", "item_label", "plan_name", "inspected_by_name"]
        read_only_fields = ["number", "result", "posted", "posted_at",
                            "voided_at", "voided_reason", "sampling"]

    def get_self_approved(self, obj):
        return obj.self_approved()

    def get_lot_status(self, obj):
        return release_status(obj.lot)


class InstrumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Instrument
        fields = ["id", "code", "name", "serial_number", "location", "interval_days",
                  "measures", "range_low", "range_high", "is_active"]


class CalibrationSerializer(serializers.ModelSerializer):
    instrument_code = serializers.CharField(source="instrument.code", read_only=True)
    suspects = serializers.SerializerMethodField()

    class Meta:
        model = Calibration
        fields = ["id", "number", "instrument", "instrument_code", "calibrated_on", "due_on",
                  "result", "performed_by", "certificate_reference", "traceable_to", "notes",
                  "posted", "posted_at", "voided_at", "voided_reason", "suspects"]

    def get_suspects(self, calibration):
        # One calibration's own page only: a list of them would ask each.
        view = self.context.get("view")
        if getattr(view, "action", None) != "retrieve":
            return None
        return [{"id": inspection.pk, "inspection": inspection.number, "lot": inspection.lot.code,
                 "inspected_on": str(inspection.inspected_on)}
                for inspection in calibration.suspect_inspections()]
        read_only_fields = ["number", "posted", "posted_at", "voided_at", "voided_reason"]
