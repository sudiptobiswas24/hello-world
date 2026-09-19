from django.contrib import admin

from apps.core.audit import AuditableAdminMixin

from .models import Bill, BillLine, GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 1


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "vendor", "order_date", "status")
    list_filter = ("status",)
    inlines = [PurchaseOrderLineInline]


class BillLineInline(admin.TabularInline):
    model = BillLine
    extra = 1

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Bill)
class BillAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "vendor", "bill_date", "posted", "debits")
    list_filter = ("posted",)
    readonly_fields = ("posted", "posted_at", "journal_entry")
    inlines = [BillLineInline]

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_delete_permission(request, obj)


class GoodsReceiptLineInline(admin.TabularInline):
    model = GoodsReceiptLine
    extra = 1

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "purchase_order", "receipt_date", "posted", "reverses")
    list_filter = ("posted",)
    readonly_fields = ("posted", "posted_at")
    inlines = [GoodsReceiptLineInline]

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_delete_permission(request, obj)
