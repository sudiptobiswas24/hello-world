from django.contrib import admin

from apps.core.admin_mixins import PostedImmutableAdminMixin, PostedImmutableInlineMixin
from apps.core.audit import AuditableAdminMixin

from .models import (
    Bill,
    BillLine,
    BillPayment,
    PrepaymentApplication,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
)


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 1
    filter_horizontal = ("taxes",)


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "vendor", "order_date", "status", "receipt_status")
    list_filter = ("status",)
    inlines = [PurchaseOrderLineInline]


class BillLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = BillLine
    extra = 1
    filter_horizontal = ("taxes",)


class BillPaymentInline(admin.TabularInline):
    model = BillPayment
    extra = 0


@admin.register(Bill)
class BillAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "number", "vendor", "bill_date", "due_date", "posted",
        "settlement_status", "debits",
    )
    list_filter = ("posted",)
    readonly_fields = ("number", "due_date", "exchange_rate", "posted", "posted_at", "journal_entry")
    inlines = [BillLineInline, BillPaymentInline]


class GoodsReceiptLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = GoodsReceiptLine
    extra = 1


@admin.register(PrepaymentApplication)
class PrepaymentApplicationAdmin(admin.ModelAdmin):
    list_display = ("prepayment", "bill", "amount", "date")
    readonly_fields = ("prepayment", "bill", "amount", "date", "journal_entry")

    def has_add_permission(self, request):
        # Drawing a prepayment down posts to the ledger, so it goes through
        # Bill.apply_prepayment() rather than a form.
        return False


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "purchase_order", "receipt_date", "posted", "reverses")
    list_filter = ("posted",)
    readonly_fields = ("number", "posted", "posted_at")
    inlines = [GoodsReceiptLineInline]
