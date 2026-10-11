from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.purchasing"
    label = "purchasing"

    def ready(self):
        from apps.accounting.models import register_bank_movements, register_payment_check, register_void_follow_up
        from apps.accounting.settlement import register_allocation_model
        from apps.sales.models import register_awaited_provider

        from .models import BillPayment, drop_ship_awaited, refuse_held_payment, undo_what_it_settled
        from .tds import challans_paid

        register_awaited_provider(drop_ship_awaited)
        register_allocation_model(BillPayment)
        register_payment_check(refuse_held_payment)
        register_void_follow_up(undo_what_it_settled)
        register_bank_movements(challans_paid)
