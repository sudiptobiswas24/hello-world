from django.contrib import admin

from apps.core.audit import AuditableAdminMixin

from .models import AssetCategory, DepreciationEntry, FixedAsset


@admin.register(AssetCategory)
class AssetCategoryAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "code", "name", "asset_account", "accumulated_account", "expense_account",
        "default_life_months", "method", "is_active",
    )
    list_filter = ("is_active", "method")


class DepreciationEntryInline(admin.TabularInline):
    model = DepreciationEntry
    extra = 0
    readonly_fields = ("period_end", "amount", "journal_entry")

    def has_add_permission(self, request, obj=None):
        # Charges are posted by depreciate(), which also writes the ledger.
        return False


@admin.register(FixedAsset)
class FixedAssetAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "number", "name", "category", "acquisition_date", "in_service_date",
        "cost", "accumulated", "net_book_value", "status",
    )
    list_filter = ("status", "category")
    readonly_fields = ("number", "disposed_on", "disposal_entry")
    inlines = [DepreciationEntryInline]
