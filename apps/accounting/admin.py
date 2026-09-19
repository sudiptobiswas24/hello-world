from django.contrib import admin

from apps.core.admin_mixins import PostedImmutableAdminMixin, PostedImmutableInlineMixin
from apps.core.audit import AuditableAdminMixin

from .models import Account, JournalEntry, JournalLine


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
