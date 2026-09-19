from django.contrib import admin

from apps.core.audit import AuditableAdminMixin

from .models import Item, StockMovement, Warehouse


@admin.register(Warehouse)
class WarehouseAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "is_active")


@admin.register(Item)
class ItemAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("sku", "name", "item_type", "uom", "track_inventory", "is_active")
    list_filter = ("item_type", "track_inventory", "is_active")
    search_fields = ("sku", "name")


@admin.register(StockMovement)
class StockMovementAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("item", "warehouse", "movement_type", "quantity", "occurred_at", "reference")
    list_filter = ("movement_type", "warehouse")
    date_hierarchy = "occurred_at"
