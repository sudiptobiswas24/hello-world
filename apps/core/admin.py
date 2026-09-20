from django.contrib import admin

from .audit import AuditableAdminMixin
from .models import (
    Address,
    Company,
    Contact,
    Country,
    Currency,
    ExchangeRate,
    Party,
    PartyBankAccount,
    PartyRoleAssignment,
    PartyTag,
    PaymentTerms,
    PaymentTermsLine,
    DocumentSequence,
    UnitOfMeasure,
)


class PartyRoleAssignmentInline(admin.TabularInline):
    model = PartyRoleAssignment
    extra = 1


class AddressInline(admin.TabularInline):
    model = Address
    extra = 0
    fields = ("address_type", "line1", "line2", "city", "state", "postal_code", "country",
              "is_primary", "is_active")


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0
    fields = ("first_name", "last_name", "job_title", "email", "phone", "is_primary", "is_active")


class PartyBankAccountInline(admin.TabularInline):
    model = PartyBankAccount
    extra = 0
    fields = ("account_name", "bank_name", "account_number", "iban", "currency", "is_primary")


@admin.register(Party)
class PartyAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "default_currency", "payment_terms", "is_active")
    list_filter = ("is_active", "tags")
    search_fields = ("code", "name", "tax_id", "email")
    filter_horizontal = ("tags",)
    inlines = [PartyRoleAssignmentInline, AddressInline, ContactInline, PartyBankAccountInline]


@admin.register(Currency)
class CurrencyAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "symbol", "decimal_places", "is_base")


@admin.register(ExchangeRate)
class ExchangeRateAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("currency", "rate", "valid_from")
    list_filter = ("currency",)
    date_hierarchy = "valid_from"


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "category", "base_unit", "conversion_factor")
    list_filter = ("category",)


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(Address)
class AddressAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("one_line", "party", "address_type", "is_primary", "is_active")
    list_filter = ("address_type", "is_active", "country")
    search_fields = ("line1", "city", "postal_code", "party__name")


@admin.register(Contact)
class ContactAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("full_name", "party", "job_title", "email", "phone", "is_primary")
    list_filter = ("is_active",)
    search_fields = ("first_name", "last_name", "email", "party__name")


@admin.register(PartyBankAccount)
class PartyBankAccountAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("account_name", "party", "bank_name", "currency", "is_primary")
    search_fields = ("account_name", "account_number", "iban", "party__name")


@admin.register(PartyTag)
class PartyTagAdmin(admin.ModelAdmin):
    list_display = ("name", "description")


class PaymentTermsLineInline(admin.TabularInline):
    model = PaymentTermsLine
    extra = 0


@admin.register(PaymentTerms)
class PaymentTermsAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "net_days", "discount_percent", "discount_days", "is_active")
    list_filter = ("is_active",)
    inlines = [PaymentTermsLineInline]


@admin.register(DocumentSequence)
class DocumentSequenceAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "prefix", "padding", "next_number", "include_year",
                    "reset_yearly", "peek")
    readonly_fields = ("current_year",)


@admin.register(Company)
class CompanyAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("name", "legal_name", "base_currency", "fiscal_year_start_month",
                    "default_inventory_account", "default_cogs_account", "grni_account")

    def has_add_permission(self, request):
        # Singleton: the profile is created on first access, never added twice.
        return not Company.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
