from django.contrib import admin

from .models import Currency, Party, PartyRoleAssignment, UnitOfMeasure


class PartyRoleAssignmentInline(admin.TabularInline):
    model = PartyRoleAssignment
    extra = 1


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "default_currency", "is_active")
    search_fields = ("code", "name", "tax_id", "email")
    inlines = [PartyRoleAssignmentInline]


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "symbol", "decimal_places")


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category")
    list_filter = ("category",)
