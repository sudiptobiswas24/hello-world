from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.purchasing"
    label = "purchasing"

    def ready(self):
        from apps.accounting.models import register_bank_movements, register_payment_check, register_void_follow_up
        from apps.accounting.settlement import register_allocation_model
        from apps.quality.release import register_lot_decided, register_lot_sources
        from apps.sales.models import register_awaited_provider

        from .models import BillPayment, assembled_from, assemblies_made_from, receipts_quality_released, drop_ship_awaited, refuse_held_payment, undo_what_it_settled
        from .tds import challans_paid

        register_awaited_provider(drop_ship_awaited)
        from apps.accounting.restatement import register_restatement_source

        def foreign_bills(day):
            from apps.core.models import Company

            from .models import Bill

            return list(Bill.objects.filter(posted=True, bill_date__lte=day, currency__isnull=False)
                        .exclude(currency_id=Company.get().base_currency_id).order_by("pk"))

        register_restatement_source(foreign_bills)
        register_allocation_model(BillPayment)
        register_payment_check(refuse_held_payment)
        register_void_follow_up(undo_what_it_settled)
        register_bank_movements(challans_paid)
        # A subcontractor's assemblies stand as the batches they were made from (O135, O148).
        register_lot_sources(assembled_from, assemblies_made_from)
        # Quality's verdict may leave a held receipt with nothing held: its to-dos close (review F4).
        register_lot_decided(receipts_quality_released)
