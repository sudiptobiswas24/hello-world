from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sales"
    label = "sales"

    def ready(self):
        from apps.accounting.models import register_dated_draft

        from .models import Delivery, Invoice

        # A period closes with no draft invoice, credit or debit note, or delivery dated in it (O361).
        register_dated_draft(Invoice, "invoice_date", lambda invoice: (
            "credit note" if invoice.is_credit_note() else "debit note" if invoice.is_debit_note() else "invoice"))
        register_dated_draft(Delivery, "delivery_date", lambda delivery: (
            "delivery return" if delivery.reverses_id else "delivery"))

        from apps.inventory.scanning import ScanDocument, register_scan_document

        from .scanning import delivery_scan_actions

        def _deliveries():
            from .views import DeliveryViewSet
            return DeliveryViewSet

        register_scan_document(ScanDocument("delivery", "delivery", _deliveries, delivery_scan_actions,
                                            open={"posted": False, "reverses__isnull": True},
                                            describe=lambda delivery: f"for {delivery.sales_order.number}"))
        from apps.accounting.models import register_void_follow_up
        from apps.accounting.settlement import register_allocation_model
        from apps.core.scoping import register_party_scope

        from .models import InvoicePayment, undo_what_it_settled
        from .scoping import SalesScope

        from apps.accounting.models import register_bank_movements

        from .tcs import tcs_challans_paid

        register_bank_movements(tcs_challans_paid)
        from apps.accounting.restatement import register_restatement_source

        def foreign_invoices(day):
            from apps.core.models import Company

            from .models import Invoice

            return list(Invoice.objects.filter(posted=True, invoice_date__lte=day, currency__isnull=False)
                        .exclude(currency_id=Company.get().base_currency_id).order_by("pk"))

        register_restatement_source(foreign_invoices)

        from apps.accounting.balances import BalanceSide, register_balance_side
        from apps.accounting.models import PaymentDirection

        from .models import INVOICE_FIGURES, Invoice, not_paid_in_full, receivable_accounts

        register_balance_side(BalanceSide(
            name="receivable", model=Invoice, row_key="invoice", rows_key="invoices", party_field="customer",
            date_field="invoice_date", notes_field="credits", figures=INVOICE_FIGURES,
            not_paid_in_full=not_paid_in_full, control_accounts=receivable_accounts,
            settles=PaymentDirection.RECEIPT, sign=1, control_field="receivable_account"))
        register_allocation_model(InvoicePayment)
        register_void_follow_up(undo_what_it_settled)
        register_party_scope(SalesScope())

        # The signed copy of a delivery, kept as a file (limit 21).
        from .proof import register as register_proof_of_delivery

        register_proof_of_delivery()
