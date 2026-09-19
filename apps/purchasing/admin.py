from django.contrib import admin

from apps.core.admin_mixins import PostedImmutableAdminMixin, PostedImmutableInlineMixin
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


class BillLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = BillLine
    extra = 1


@admin.register(Bill)
class BillAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "vendor", "bill_date", "posted", "debits")
    list_filter = ("posted",)
    readonly_fields = ("posted", "posted_at", "journal_entry")
    inlines = [BillLineInline]


class GoodsReceiptLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = GoodsReceiptLine
    extra = 1


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "purchase_order", "receipt_date", "posted", "reverses")
    list_filter = ("posted",)
    readonly_fields = ("posted", "posted_at")
    inlines = [GoodsReceiptLineInline]
