from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sales"
    label = "sales"

    def ready(self):
        from apps.accounting.settlement import register_allocation_model

        from .models import InvoicePayment

        register_allocation_model(InvoicePayment)
