from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.purchasing"
    label = "purchasing"

    def ready(self):
        from apps.accounting.settlement import register_allocation_model
        from apps.sales.models import register_awaited_provider

        from .models import BillPayment, drop_ship_awaited

        register_awaited_provider(drop_ship_awaited)
        register_allocation_model(BillPayment)
