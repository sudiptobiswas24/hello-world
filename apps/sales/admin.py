from django.contrib import admin

from apps.core.admin_mixins import PostedImmutableAdminMixin, PostedImmutableInlineMixin
from apps.core.audit import AuditableAdminMixin

from .models import (
    CustomerProfile,
    Delivery,
    DunningLevel,
    DunningNotice,
    DeliveryLine,
    Invoice,
    InvoiceLine,
    InvoicePayment,
    PriceList,
    PriceListItem,
    Quotation,
    QuotationLine,
    SalesOrder,
    SalesOrderLine,
)


class SalesOrderLineInline(admin.TabularInline):
    model = SalesOrderLine
    extra = 1
    fields = ("item", "uom", "quantity", "unit_price", "discount_percent", "revenue_account", "taxes")
    filter_horizontal = ("taxes",)


@admin.register(SalesOrder)
class SalesOrderAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "customer", "order_date", "status", "total",
                    "invoice_status", "delivery_status")
    list_filter = ("status",)
    search_fields = ("number", "reference", "customer__name")
    readonly_fields = ("number",)
    inlines = [SalesOrderLineInline]


class InvoicePaymentInline(admin.TabularInline):
    model = InvoicePayment
    extra = 0
    fields = ("payment", "amount")


class InvoiceLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = InvoiceLine
    extra = 1
    fields = ("order_line", "credits_line", "item", "description", "quantity",
              "unit_price", "discount_percent", "revenue_account", "taxes")
    filter_horizontal = ("taxes",)


@admin.register(Invoice)
class InvoiceAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "customer", "invoice_date", "due_date", "currency",
                    "total", "amount_due", "settlement_status", "posted")
    list_filter = ("posted", "currency")
    search_fields = ("number", "reference", "customer__name")
    readonly_fields = ("number", "due_date", "exchange_rate", "posted", "posted_at",
                       "journal_entry")
    inlines = [InvoiceLineInline, InvoicePaymentInline]


class DeliveryLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = DeliveryLine
    extra = 1
    fields = ("order_line", "warehouse", "quantity_shipped")


@admin.register(Delivery)
class DeliveryAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "sales_order", "delivery_date", "posted", "reverses")
    list_filter = ("posted",)
    search_fields = ("number", "reference")
    readonly_fields = ("number", "posted", "posted_at", "reverses")
    inlines = [DeliveryLineInline]


class PriceListItemInline(admin.TabularInline):
    model = PriceListItem
    extra = 1
    fields = ("item", "min_quantity", "unit_price")


@admin.register(PriceList)
class PriceListAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "currency", "is_default", "valid_from", "valid_to", "is_active")
    list_filter = ("is_default", "is_active", "currency")
    inlines = [PriceListItemInline]


@admin.register(CustomerProfile)
class CustomerProfileAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("party", "price_list", "credit_limit")
    search_fields = ("party__name", "party__code")


class QuotationLineInline(admin.TabularInline):
    model = QuotationLine
    extra = 1
    fields = ("item", "uom", "quantity", "unit_price", "discount_percent",
              "revenue_account", "taxes")
    filter_horizontal = ("taxes",)


@admin.register(Quotation)
class QuotationAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "customer", "quotation_date", "valid_until", "status",
                    "total", "sales_order")
    list_filter = ("status",)
    search_fields = ("number", "reference", "customer__name")
    readonly_fields = ("number", "status", "sales_order", "sent_at")
    inlines = [QuotationLineInline]


@admin.register(DunningLevel)
class DunningLevelAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("name", "days_overdue", "is_active")
    list_filter = ("is_active",)


@admin.register(DunningNotice)
class DunningNoticeAdmin(admin.ModelAdmin):
    list_display = ("invoice", "level", "days_overdue", "amount_due", "sent_to", "sent_at")
    list_filter = ("level",)
    readonly_fields = ("invoice", "level", "days_overdue", "amount_due", "sent_to", "sent_at")

    def has_add_permission(self, request):
        return False
