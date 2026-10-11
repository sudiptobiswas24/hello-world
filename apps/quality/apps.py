from django.apps import AppConfig


class QualityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.quality"
    label = "quality"

    def ready(self):
        from apps.inventory.tracking import register_lot_hold

        from .release import why_held

        # A pick that chooses batches for itself asks the release gate
        # too, so it never chooses one a person could not have named.
        register_lot_hold(why_held)
