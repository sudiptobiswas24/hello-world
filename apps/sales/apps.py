from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sales"
    label = "sales"

    def ready(self):
        from apps.accounting.models import register_void_follow_up
        from apps.accounting.settlement import register_allocation_model
        from apps.core.scoping import register_party_scope

        from .models import InvoicePayment, withdraw_discounts_a_void_unearned
        from .scoping import SalesScope

        register_allocation_model(InvoicePayment)
        register_void_follow_up(withdraw_discounts_a_void_unearned)
        register_party_scope(SalesScope())
