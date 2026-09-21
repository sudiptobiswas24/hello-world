from django.contrib import admin

from apps.core.audit import AuditableAdminMixin

from .models import (
    AdjustmentReason,
    Item,
    Lot,
    StockAdjustment,
    StockAdjustmentLine,
    StockCount,
    StockCountLine,
    StockMovement,
    StockReservation,
    StockTransfer,
    StockTransferLine,
    StockTransferStep,
    StorageBin,
    Warehouse,
)


@admin.register(Warehouse)
class WarehouseAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "is_quarantine", "is_transit", "requires_bins",
                    "allow_negative_stock", "consignment_vendor", "is_active")
    list_filter = ("is_quarantine", "is_transit", "requires_bins", "is_active")
    search_fields = ("code", "name")


@admin.register(Item)
class ItemAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("sku", "name", "item_type", "uom", "track_inventory",
                    "tracking", "costing_method", "standard_cost",
                    "inventory_account", "cogs_account", "is_active")
    list_filter = ("item_type", "track_inventory", "tracking", "costing_method",
                   "is_active")
    search_fields = ("sku", "name")
    # Changing a standard revalues the stock on hand, which is a posting.
    # set_standard_cost() does that; typing over the field would move the
    # shelf's value with nothing in the ledger behind it.
    readonly_fields = ("standard_cost",)


@admin.register(StockMovement)
class StockMovementAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("item", "warehouse", "movement_type", "lot", "bin", "quantity",
                    "unit_cost", "occurred_at", "reference")
    list_filter = ("movement_type", "warehouse")
    search_fields = ("item__sku", "reference", "lot__code")
    date_hierarchy = "occurred_at"
    autocomplete_fields = ("item", "lot")


@admin.register(Lot)
class LotAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "item", "expires_on", "manufactured_on",
                    "supplier_reference", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "item__sku", "supplier_reference")
    autocomplete_fields = ("item",)
    date_hierarchy = "expires_on"


@admin.register(StorageBin)
class StorageBinAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "warehouse", "parent", "sequence", "is_pickable",
                    "is_active")
    list_filter = ("warehouse", "is_pickable", "is_active")
    search_fields = ("code", "name")


@admin.register(AdjustmentReason)
class AdjustmentReasonAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "account", "direction", "is_active")
    list_filter = ("direction", "is_active")


class PostedDocumentAdminMixin:
    """
    A posted document is read-only here.

    The models refuse the edit anyway, so without this the admin offers a
    form that always fails to save — which teaches people the system is
    broken rather than that the document is final.
    """

    posted_field = "posted"

    def _is_posted(self, obj):
        return bool(obj and getattr(obj, self.posted_field, False))

    def has_change_permission(self, request, obj=None):
        if self._is_posted(obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if self._is_posted(obj):
            return False
        return super().has_delete_permission(request, obj)


class StockAdjustmentLineInline(admin.TabularInline):
    model = StockAdjustmentLine
    extra = 1
    autocomplete_fields = ("item", "lot")
    readonly_fields = ("unit_cost", "movement", "reversal_movement")


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(PostedDocumentAdminMixin, AuditableAdminMixin,
                           admin.ModelAdmin):
    list_display = ("__str__", "adjustment_date", "warehouse", "reason", "posted",
                    "voided_at", "total_value")
    list_filter = ("posted", "warehouse", "reason")
    search_fields = ("number", "memo")
    date_hierarchy = "adjustment_date"
    inlines = [StockAdjustmentLineInline]
    readonly_fields = ("number", "posted", "posted_at", "journal_entry",
                       "voided_entry", "voided_at", "count", "from_standard_change")


class StockCountLineInline(admin.TabularInline):
    model = StockCountLine
    extra = 1
    autocomplete_fields = ("item", "lot")
    # Frozen when the line is written: re-reading it at posting time would
    # hide anything that moved in between, which is the whole point of the
    # sheet.
    readonly_fields = ("system_quantity",)


@admin.register(StockCount)
class StockCountAdmin(PostedDocumentAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "count_date", "warehouse", "reason", "counted_by",
                    "posted")
    list_filter = ("posted", "warehouse")
    search_fields = ("number", "memo", "counted_by")
    date_hierarchy = "count_date"
    inlines = [StockCountLineInline]
    readonly_fields = ("number", "posted", "posted_at")


class StockTransferLineInline(admin.TabularInline):
    model = StockTransferLine
    extra = 1
    autocomplete_fields = ("item", "lot")


class StockTransferStepInline(admin.TabularInline):
    model = StockTransferStep
    extra = 0
    can_delete = False
    readonly_fields = ("leg", "quantity", "source", "destination", "out_movement",
                       "in_movement", "reversed_step", "occurred_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(StockTransfer)
class StockTransferAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "transfer_date", "from_warehouse", "to_warehouse",
                    "transit_warehouse", "status")
    list_filter = ("status", "from_warehouse", "to_warehouse")
    search_fields = ("number", "reference", "memo")
    date_hierarchy = "transfer_date"
    inlines = [StockTransferLineInline]
    readonly_fields = ("number", "status", "dispatched_at", "received_at",
                       "cancelled_at")

    def has_change_permission(self, request, obj=None):
        # Anything past draft has already moved stock.
        if obj is not None and obj.status != "draft":
            return False
        return super().has_change_permission(request, obj)


@admin.register(StockReservation)
class StockReservationAdmin(admin.ModelAdmin):
    """
    Read-only: a reservation is made and released by the document that
    holds it, and one created here is a claim nothing will ever give
    back.
    """

    list_display = ("item", "warehouse", "quantity", "consumed", "released_at",
                    "released_reason")
    list_filter = ("warehouse",)
    search_fields = ("item__sku",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
