from django.contrib import admin

from apps.core.audit import AuditableAdminMixin

from .models import Account, JournalEntry, JournalLine


@admin.register(Account)
class AccountAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "account_type", "parent", "currency", "is_active")
    list_filter = ("account_type", "is_active")
    search_fields = ("code", "name")


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 2
    fields = ("account", "party", "debit", "credit", "description")

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(JournalEntry)
class JournalEntryAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "date", "reference", "memo", "posted", "posted_at")
    list_filter = ("posted",)
    readonly_fields = ("posted", "posted_at", "reverses")
    inlines = [JournalLineInline]

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_delete_permission(request, obj)
