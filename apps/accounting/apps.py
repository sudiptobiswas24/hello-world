from django.apps import AppConfig


class AccountingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounting"
    label = "accounting"

    def ready(self):
        from apps.core.models import register_booked_in_base, register_company_check

        from .models import JournalEntry
        from .money import refuse_as_money_account, refuse_money_kept_as, settings_fields

        def posted_entries():
            return "A posted journal entry" if JournalEntry.objects.filter(posted=True).exists() else ""

        def money_settings(company):
            # Every payment that names no bank goes through the default one, so
            # it is asked what a payment's own bank is asked, as it is set; and
            # none of the other settings is where the money is.
            stored = type(company).objects.filter(pk=company.pk).values_list(
                "default_bank_account_id", flat=True).first() if company.pk else None
            if company.default_bank_account_id != stored:
                refuse_as_money_account(company.default_bank_account, "default_bank_account")
            refuse_money_kept_as(company, *settings_fields(type(company)))

        register_booked_in_base(posted_entries)
        register_company_check(money_settings)
