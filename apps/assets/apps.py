from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assets"
    verbose_name = "Fixed assets"

    def ready(self):
        from apps.accounting.models import register_close_check

        from .models import depreciation_owed

        # A period closes with every month's charge in it (O29).
        register_close_check(depreciation_owed)
