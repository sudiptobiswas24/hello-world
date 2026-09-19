from django.contrib import admin

from .audit import AuditableAdminMixin
from .models import Currency, Party, PartyRoleAssignment, UnitOfMeasure


class PartyRoleAssignmentInline(admin.TabularInline):
    model = PartyRoleAssignment
    extra = 1


@admin.register(Party)
class PartyAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "default_currency", "is_active")
    search_fields = ("code", "name", "tax_id", "email")
    inlines = [PartyRoleAssignmentInline]


@admin.register(Currency)
class CurrencyAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "symbol", "decimal_places", "is_base")


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "category", "base_unit", "conversion_factor")
    list_filter = ("category",)
