from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"
    label = "inventory"

    def ready(self):
        from apps.core.models import register_company_check

        from .valuation import (
            refuse_a_default_cogs_account_that_is_not_one,
            refuse_a_default_stock_account_that_is_not_one,
            refuse_moving_the_default_stock_account,
        )

        register_company_check(refuse_moving_the_default_stock_account)
        register_company_check(refuse_a_default_stock_account_that_is_not_one)
        register_company_check(refuse_a_default_cogs_account_that_is_not_one)

        # An asset on a gate pass is not disposed of (g25 review F1): asked by assets, which imports nothing of ours.
        from apps.assets.models import register_disposal_guard

        from .gate import refuse_disposing_of_an_asset_on_a_pass

        register_disposal_guard(refuse_disposing_of_an_asset_on_a_pass)
