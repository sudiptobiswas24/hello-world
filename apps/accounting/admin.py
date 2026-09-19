from django.contrib import admin

from apps.core.admin_mixins import PostedImmutableAdminMixin, PostedImmutableInlineMixin
from apps.core.audit import AuditableAdminMixin

from .models import (
    Account,
    FiscalPosition,
    FiscalPositionTaxMapping,
    JournalEntry,
    JournalLine,
    PartyTaxProfile,
    Payment,
    Tax,
    TaxGroup,
)


@admin.register(Account)
class AccountAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "account_type", "parent", "currency", "is_active")
    list_filter = ("account_type", "is_active")
    search_fields = ("code", "name")


class JournalLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = JournalLine
    extra = 2
    fields = ("account", "party", "debit", "credit", "description")


@admin.register(JournalEntry)
class JournalEntryAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "date", "reference", "memo", "posted", "posted_at")
    list_filter = ("posted",)
    readonly_fields = ("posted", "posted_at", "reverses")
    inlines = [JournalLineInline]


@admin.register(TaxGroup)
class TaxGroupAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name")


@admin.register(Tax)
class TaxAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "code", "name", "computation", "rate", "scope", "price_included",
        "include_base_amount", "sequence", "is_active",
    )
    list_filter = ("scope", "computation", "price_included", "is_active")
    search_fields = ("code", "name")


class FiscalPositionTaxMappingInline(admin.TabularInline):
    model = FiscalPositionTaxMapping
    extra = 1
    fields = ("source_tax", "target_tax")


@admin.register(FiscalPosition)
class FiscalPositionAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "country", "is_active")
    list_filter = ("is_active", "country")
    inlines = [FiscalPositionTaxMappingInline]


@admin.register(PartyTaxProfile)
class PartyTaxProfileAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("party", "fiscal_position", "tax_exempt", "exemption_reference")
    list_filter = ("tax_exempt", "fiscal_position")
    search_fields = ("party__name", "party__code", "exemption_reference")


@admin.register(Payment)
class PaymentAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "party", "direction", "payment_date", "amount", "currency", "posted")
    list_filter = ("direction", "posted", "currency")
    search_fields = ("number", "reference", "party__name")
    readonly_fields = ("number", "exchange_rate", "posted", "posted_at", "journal_entry")
