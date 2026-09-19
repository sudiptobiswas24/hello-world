from django.contrib import admin

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


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 1

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Invoice)
class InvoiceAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "customer", "invoice_date", "posted", "credits")
    list_filter = ("posted",)
    readonly_fields = ("posted", "posted_at", "journal_entry")
    inlines = [InvoiceLineInline]

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_delete_permission(request, obj)
