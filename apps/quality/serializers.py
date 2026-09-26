from rest_framework import serializers

from .models import (
    Characteristic,
    Inspection,
    InspectionPlan,
    PlanLine,
    Reading,
)
from .release import release_status


class CharacteristicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Characteristic
        fields = ["id", "code", "name", "kind", "uom", "decimal_places",
                  "is_active"]


class PlanLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanLine
        fields = ["id", "plan", "characteristic", "target", "lower_limit",
                  "upper_limit", "sample_size", "evaluation", "derived_from",
                  "line_number"]
        read_only_fields = ["derived_from"]


class InspectionPlanSerializer(serializers.ModelSerializer):
    lines = PlanLineSerializer(many=True, read_only=True)

    class Meta:
        model = InspectionPlan
        fields = ["id", "item", "name", "is_mandatory", "is_computed",
                  "is_active", "notes", "lines"]
        read_only_fields = ["is_computed"]


class ReadingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reading
        fields = ["id", "inspection", "plan_line", "value", "present",
                  "sample_reference", "lower_limit", "upper_limit", "passed"]
        read_only_fields = ["lower_limit", "upper_limit", "passed"]


class InspectionSerializer(serializers.ModelSerializer):
    readings = ReadingSerializer(many=True, read_only=True)
    self_approved = serializers.SerializerMethodField()
    lot_status = serializers.SerializerMethodField()

    class Meta:
        model = Inspection
        fields = ["id", "number", "lot", "plan", "inspected_on", "inspected_by",
                  "disposition", "decided_by", "decision_note", "result",
                  "posted", "posted_at", "voided_at", "voided_reason", "notes",
                  "readings", "self_approved", "lot_status"]
        read_only_fields = ["number", "result", "posted", "posted_at",
                            "voided_at", "voided_reason"]

    def get_self_approved(self, obj):
        return obj.self_approved()

    def get_lot_status(self, obj):
        return release_status(obj.lot)
