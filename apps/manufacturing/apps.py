from django.apps import AppConfig


class ManufacturingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.manufacturing"
    label = "manufacturing"

    def ready(self):
        from apps.inventory.models import register_unit_provider
        from apps.sales.models import register_material_checker

        from .compliance import material_problems
        from .woven import sack_units

        register_unit_provider(sack_units)
        register_material_checker(material_problems)
