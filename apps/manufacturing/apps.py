from django.apps import AppConfig


class ManufacturingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.manufacturing"
    label = "manufacturing"

    def ready(self):
        from apps.hr.payroll import register_piece_measure
        from apps.inventory.models import register_unit_provider
        from apps.sales.models import register_material_checker, register_ownership_checker

        from .compliance import material_problems
        from .inward import ownership_problems
        from .piecework import kilograms_woven, metres_woven
        from .woven import sack_units

        register_unit_provider(sack_units)
        register_material_checker(material_problems)
        register_ownership_checker(ownership_problems)
        register_piece_measure("metres_woven", "Metres woven", metres_woven)
        register_piece_measure("kilograms_woven", "Kilograms woven", kilograms_woven)
