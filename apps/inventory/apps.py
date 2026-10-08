from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"
    label = "inventory"

    def ready(self):
        from apps.core.models import register_company_check

        from .valuation import refuse_moving_the_default_stock_account

        register_company_check(refuse_moving_the_default_stock_account)
