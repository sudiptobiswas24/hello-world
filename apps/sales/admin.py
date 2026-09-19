from django.contrib import admin

from apps.core.admin_mixins import PostedImmutableAdminMixin, PostedImmutableInlineMixin
from apps.core.audit import AuditableAdminMixin

from .models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine


class SalesOrderLineInline(admin.TabularInline):
    model = SalesOrderLine
    extra = 1


@admin.register(SalesOrder)
class SalesOrderAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "customer", "order_date", "status")
    list_filter = ("status",)
    inlines = [SalesOrderLineInline]


class InvoiceLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = InvoiceLine
    extra = 1


@admin.register(Invoice)
class InvoiceAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "customer", "invoice_date", "posted", "credits")
    list_filter = ("posted",)
    readonly_fields = ("posted", "posted_at", "journal_entry")
    inlines = [InvoiceLineInline]
