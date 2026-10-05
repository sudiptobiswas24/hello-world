from django.apps import AppConfig


class HrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.hr"
    label = "hr"

    def ready(self):
        from apps.core.scoping import register_user_party
        from apps.core.views import register_me_extra

        from .scoping import party_of_login
        from .views import me_as_employee

        register_me_extra(me_as_employee)
        register_user_party(party_of_login)
