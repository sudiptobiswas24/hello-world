from django.apps import AppConfig


class HrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.hr"
    label = "hr"

    def ready(self):
        from apps.core.models import register_working_day

        from .calendars import plant_works_on

        # The plant's holidays, for whoever counts working days without importing hr (O203).
        register_working_day(plant_works_on)
        from apps.core.models import register_login_end

        from .models import leaver_login

        # A leaver's login stops from the day after their leaving date (O300).
        register_login_end(leaver_login)
        from apps.accounting.models import register_bank_movements
        from apps.core.scoping import register_user_party
        from apps.core.views import register_me_extra

        from .expenses import claims_paid
        from .scoping import party_of_login
        from .views import me_as_employee

        register_me_extra(me_as_employee)
        register_user_party(party_of_login)
        register_bank_movements(claims_paid)

        from apps.accounting.models import register_void_follow_up

        from .advances import refuse_void_of_a_recovered_advance

        register_void_follow_up(refuse_void_of_a_recovered_advance)

        from apps.accounting.models import register_payment_check

        from .shortfalls import hold_the_payer

        # Money from an employee holds them as it posts, as a write-off of what they owe does (O270).
        register_payment_check(hold_the_payer)

        from .bank_file import refuse_void_under_a_returned_transfer

        register_void_follow_up(refuse_void_under_a_returned_transfer)

        from apps.accounting.models import register_close_check

        from .payroll import pay_runs_owed

        # A period closes with its pay runs posted (O310).
        register_close_check(pay_runs_owed)
