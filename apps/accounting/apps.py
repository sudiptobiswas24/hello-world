from django.apps import AppConfig


class AccountingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounting"
    label = "accounting"

    def ready(self):
        from apps.core.models import register_booked_in_base

        from .models import JournalEntry

        def posted_entries():
            return "A posted journal entry" if JournalEntry.objects.filter(posted=True).exists() else ""

        register_booked_in_base(posted_entries)
