from rest_framework import serializers

from .calibration import Calibration, Instrument
from .models import (
    Characteristic,
    Inspection,
    InspectionPlan,
    PlanLine,
    Reading,
)
from .certificates import SupplierCertificate, SupplierCertificateLine
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
        fields = ["id", "item", "item_label", "lines_count", "name", "is_mandatory", "certificate_required",
                  "is_computed",
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
    disposition_label = serializers.CharField(source="get_disposition_display", read_only=True)
    decided_by_name = serializers.CharField(source="decided_by.name", read_only=True, default="")
    decision_login_name = serializers.CharField(source="decision_login.username", read_only=True,
                                                default="")
    decision_due_on = serializers.SerializerMethodField()
    # Whether posting this draft as a release would lift a verdict only the Quality Manager may
    # end, so the screen offers a release to them alone (O241). The server asks it again.
    releasing_is_theirs = serializers.SerializerMethodField()

    def get_releasing_is_theirs(self, inspection):
        from django.contrib.auth.models import AnonymousUser
        from django.core.exceptions import ValidationError

        from .release import latest_inspection

        if inspection.posted:
            return False
        standing = latest_inspection(inspection.lot)
        if standing is None:
            return False
        try:
            standing.check_lifted_by(AnonymousUser(), reinspection=True)
        except ValidationError:
            return True
        return False

    class Meta:
        model = Inspection
        fields = ["id", "number", "lot", "plan", "inspected_on", "inspected_by",
                  "disposition", "decided_by", "decision_note", "result",
                  "posted", "posted_at", "voided_at", "voided_reason", "notes",
                  "readings", "self_approved", "lot_status", "lot_size", "sampling",
                  "lot_code", "item_label", "plan_name", "inspected_by_name",
                  "disposition_label", "decided_by_name", "decided_at", "decision_login",
                  "decision_login_name", "told", "escalated_on", "decision_due_on", "releasing_is_theirs"]
        # decided_by is the login that posts a concession (Inspection.post), never typed;
        # the rest of the decision is the Quality Manager's, taken by decide/.
        read_only_fields = ["number", "result", "posted", "posted_at",
                            "voided_at", "voided_reason", "sampling", "decided_by",
                            "decided_at", "decision_login", "told", "escalated_on"]

    def get_decision_due_on(self, obj):
        return obj.decision_due_on() if obj.waits_for_a_decision() else None

    def get_self_approved(self, obj):
        return obj.self_approved()

    def get_lot_status(self, obj):
        return release_status(obj.lot)


class InstrumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Instrument
        fields = ["id", "code", "name", "serial_number", "location", "interval_days",
                  "measures", "range_low", "range_high", "is_active", "is_weighbridge"]
        read_only_fields = ["is_weighbridge"]


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


class SupplierCertificateLineSerializer(serializers.ModelSerializer):
    characteristic_label = serializers.SerializerMethodField()
    missing = serializers.SerializerMethodField()

    def get_characteristic_label(self, line):
        return f"{line.characteristic.code} · {line.characteristic.name}"

    def get_missing(self, line):
        return line.is_missing()

    value_shown = serializers.SerializerMethodField()
    limits_shown = serializers.SerializerMethodField()

    def get_value_shown(self, line):
        """As the supplier printed it, without the six places the column keeps: 4.1, not 4.100000."""
        return line.shown_value()

    def get_limits_shown(self, line):
        return line.limit_words() if (line.lower_limit is not None or line.upper_limit is not None) else ""

    class Meta:
        model = SupplierCertificateLine
        fields = ["id", "certificate", "characteristic", "characteristic_label", "value", "present",
                  "lower_limit", "upper_limit", "within", "missing", "value_shown", "limits_shown"]
        read_only_fields = ["lower_limit", "upper_limit", "within"]


class SupplierCertificateSerializer(serializers.ModelSerializer):
    lines = SupplierCertificateLineSerializer(many=True, read_only=True)
    lot_code = serializers.CharField(source="lot.code", read_only=True)
    item_label = serializers.SerializerMethodField()
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    plan_name = serializers.CharField(source="plan.__str__", read_only=True, default="")
    result_label = serializers.CharField(source="get_result_display", read_only=True)
    verdict = serializers.SerializerMethodField()
    lot_held_because = serializers.SerializerMethodField()

    def get_item_label(self, certificate):
        return f"{certificate.lot.item.sku} · {certificate.lot.item.name}"

    def get_verdict(self, certificate):
        return certificate.verdict_words() if certificate.posted else ""

    def get_lot_held_because(self, certificate):
        from .release import still_held_words

        return still_held_words(certificate.lot)

    class Meta:
        model = SupplierCertificate
        fields = ["id", "number", "lot", "lot_code", "item_label", "vendor", "vendor_name", "supplier_number",
                  "issued_on", "recorded_on", "plan", "plan_name", "result", "result_label", "verdict",
                  "posted", "posted_at", "posted_by", "voided_on", "voided_at", "voided_reason", "voided_by",
                  "notes", "lines", "lot_held_because"]
        read_only_fields = ["number", "plan", "result", "posted", "posted_at", "posted_by", "voided_on",
                            "voided_at", "voided_reason", "voided_by"]
        # Filled in save() from the receipt that brought the batch in, and the plant's today.
        extra_kwargs = {"vendor": {"required": False}, "recorded_on": {"required": False}}
