from django.contrib import admin

from apps.core.admin_mixins import PostedImmutableAdminMixin, PostedImmutableInlineMixin
from apps.core.audit import AuditableAdminMixin

from .models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine


class SalesOrderLineInline(admin.TabularInline):
    model = SalesOrderLine
    extra = 1
    fields = ("item", "uom", "quantity", "unit_price", "discount_percent", "revenue_account", "taxes")
    filter_horizontal = ("taxes",)


@admin.register(SalesOrder)
class SalesOrderAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "customer", "order_date", "status", "total")
    list_filter = ("status",)
    search_fields = ("number", "reference", "customer__name")
    readonly_fields = ("number",)
    inlines = [SalesOrderLineInline]


class InvoiceLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = InvoiceLine
    extra = 1
    fields = ("item", "description", "quantity", "unit_price", "discount_percent",
              "revenue_account", "taxes")
    filter_horizontal = ("taxes",)


@admin.register(Invoice)
class InvoiceAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "customer", "invoice_date", "due_date", "currency",
                    "total", "posted", "credits")
    list_filter = ("posted", "currency")
    search_fields = ("number", "reference", "customer__name")
    readonly_fields = ("number", "due_date", "exchange_rate", "posted", "posted_at",
                       "journal_entry")
    inlines = [InvoiceLineInline]
