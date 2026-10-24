from django.apps import AppConfig


class PlanningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.planning"
    label = "planning"

    def ready(self):
        from apps.core.models import register_working_day

        from .leadtime import plant_is_open

        # The plant's own week and holiday region answer whether it works a day (review F6),
        # for whoever counts working days without importing planning (quality's escalation).
        register_working_day(plant_is_open)
