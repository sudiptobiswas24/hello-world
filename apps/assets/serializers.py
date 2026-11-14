from rest_framework import serializers

from .models import AssetCategory, DepreciationEntry, FixedAsset
from apps.core.customfields import ExtensibleSerializerMixin


class AssetCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetCategory
        fields = ["id", "code", "name", "asset_account", "accumulated_account",
                  "expense_account", "disposal_account", "default_life_months",
                  "method", "is_active", "tax_block"]


class DepreciationEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = DepreciationEntry
        fields = ["id", "asset", "period_end", "amount", "journal_entry", "reversal"]
        read_only_fields = fields


class FixedAssetSerializer(ExtensibleSerializerMixin, serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    accumulated = serializers.SerializerMethodField()
    net_book_value = serializers.SerializerMethodField()
    monthly_charge = serializers.SerializerMethodField()
    out_on_pass = serializers.SerializerMethodField()
    damaged_on_return = serializers.SerializerMethodField()

    class Meta:
        model = FixedAsset
        fields = ["id", "number", "name", "category", "vendor", "bill_line",
                  "acquisition_date", "in_service_date", "cost",
                  "salvage_value", "life_months", "depreciated_before", "opening_depreciation", "status", "disposed_on",
                  "capitalisation_entry", "disposal_entry", "reinstatement_entry", "accumulated",
                  "net_book_value", "monthly_charge", "category_name", "vendor_name", "extra",
                  "asset_account", "accumulated_account", "method", "tax_block", "disposal_proceeds",
                  "out_on_pass", "damaged_on_return"]
        read_only_fields = ["number", "bill_line", "status", "disposed_on",
                            "capitalisation_entry", "disposal_entry", "reinstatement_entry",
                            "asset_account", "accumulated_account", "method", "disposal_proceeds"]

    def get_damaged_on_return(self, obj):
        """
        Came back damaged on a gate pass: the register asks for an impairment review. Nothing is written down
        from the pass; a write-down is the register's own decision.
        """
        rows = [entry for line in obj.gate_pass_lines.all()
                for entry in line.returns.filter(condition="damaged", cancelled_at__isnull=True)]
        if not rows:
            return None
        latest = max(rows, key=lambda entry: (entry.returned_on, entry.pk))
        return {"pass": latest.line.gate_pass.number, "returned_on": latest.returned_on, "note": latest.note,
                "impairment_review": True}

    def get_out_on_pass(self, obj):
        """The returnable gate pass it is out on (L18), read through the pass's own pointer: out until back."""
        for line in obj.gate_pass_lines.filter(gate_pass__status="out", gate_pass__kind="returnable"):
            if line.outstanding() > 0:
                gate_pass = line.gate_pass
                return {"id": gate_pass.pk, "number": gate_pass.number, "party": gate_pass.party_name,
                        "expected_return_date": gate_pass.expected_return_date}
        return None

    def get_accumulated(self, obj):
        return obj.accumulated()

    def get_net_book_value(self, obj):
        return obj.net_book_value()

    def get_monthly_charge(self, obj):
        return obj.monthly_charge()
