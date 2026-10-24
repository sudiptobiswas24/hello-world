from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"
    label = "inventory"

    def ready(self):
        from apps.core.models import register_company_check

        from .valuation import refuse_a_default_stock_account_that_is_not_one, refuse_moving_the_default_stock_account

        register_company_check(refuse_moving_the_default_stock_account)
        register_company_check(refuse_a_default_stock_account_that_is_not_one)
