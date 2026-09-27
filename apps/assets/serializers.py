from rest_framework import serializers

from .models import AssetCategory, DepreciationEntry, FixedAsset


class AssetCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetCategory
        fields = ["id", "code", "name", "asset_account", "accumulated_account",
                  "expense_account", "disposal_account", "default_life_months",
                  "method", "is_active"]


class DepreciationEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = DepreciationEntry
        fields = ["id", "asset", "period_end", "amount", "journal_entry"]
        read_only_fields = fields


class FixedAssetSerializer(serializers.ModelSerializer):
    accumulated = serializers.SerializerMethodField()
    net_book_value = serializers.SerializerMethodField()
    monthly_charge = serializers.SerializerMethodField()

    class Meta:
        model = FixedAsset
        fields = ["id", "number", "name", "category", "vendor", "bill_line",
                  "acquisition_date", "in_service_date", "cost",
                  "salvage_value", "life_months", "status", "disposed_on",
                  "capitalisation_entry", "disposal_entry", "accumulated",
                  "net_book_value", "monthly_charge"]
        read_only_fields = ["number", "bill_line", "status", "disposed_on",
                            "capitalisation_entry", "disposal_entry"]

    def get_accumulated(self, obj):
        return obj.accumulated()

    def get_net_book_value(self, obj):
        return obj.net_book_value()

    def get_monthly_charge(self, obj):
        return obj.monthly_charge()
