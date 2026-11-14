from django.apps import AppConfig
from django.db.models import Q


class PurchasingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.purchasing"
    label = "purchasing"

    def ready(self):
        from django.db.models.signals import post_migrate

        from apps.accounting.models import register_dated_draft

        from .msme import keep_unkept_due_dates

        # A micro or small vendor's bill posted before migration 0090 is due by the Act's day (O498).
        post_migrate.connect(keep_unkept_due_dates, sender=self, dispatch_uid="apps.purchasing.keep_due_dates")

        from .imports import BillOfEntry, EntryStatus
        from .models import Bill, GoodsReceipt

        # A period closes with no draft bill, debit note, receipt or bill of entry dated in it (O361).
        register_dated_draft(Bill, "bill_date", lambda bill: "debit note" if bill.is_debit_note() else "bill")
        register_dated_draft(GoodsReceipt, "receipt_date", lambda receipt: (
            "goods receipt return" if receipt.reverses_id else "goods receipt"))
        register_dated_draft(BillOfEntry, "be_date", lambda entry: "bill of entry", Q(status=EntryStatus.DRAFT))

        from apps.inventory.scanning import ScanDocument, register_scan_document

        from .scanning import order_scan_actions, receipt_scan_actions

        def _receipts():
            from .views import GoodsReceiptViewSet
            return GoodsReceiptViewSet

        def _orders():
            from .views import PurchaseOrderViewSet
            return PurchaseOrderViewSet

        register_scan_document(ScanDocument("goods_receipt", "receipt", _receipts, receipt_scan_actions,
                                            open={"posted": False, "reverses__isnull": True},
                                            describe=lambda receipt: f"against {receipt.purchase_order.number}"))
        register_scan_document(ScanDocument("purchase_order", "purchase order", _orders, order_scan_actions,
                                            state=lambda order: order.status, open={"status": "confirmed"},
                                            describe=lambda order: str(order.vendor)))
        from apps.accounting.models import register_bank_movements, register_payment_check, register_void_follow_up
        from apps.accounting.settlement import register_allocation_model
        from apps.quality.release import register_lot_decided, register_lot_sources, register_lot_supplier
        from apps.sales.models import register_awaited_provider

        from .models import BillPayment, received_from, assembled_from, assemblies_made_from, receipts_quality_released, drop_ship_awaited, refuse_held_payment, undo_what_it_settled
        from .tds import challans_paid

        register_awaited_provider(drop_ship_awaited)
        from apps.accounting.restatement import register_restatement_source

        def foreign_bills(day):
            from apps.core.models import Company

            from .models import Bill

            return list(Bill.objects.filter(posted=True, bill_date__lte=day, currency__isnull=False)
                        .exclude(currency_id=Company.get().base_currency_id).order_by("pk"))

        register_restatement_source(foreign_bills)

        from apps.accounting.balances import BalanceSide, register_balance_side
        from apps.accounting.models import PaymentDirection

        from .models import BILL_FIGURES, Bill, not_paid_in_full, payable_accounts

        register_balance_side(BalanceSide(
            name="payable", model=Bill, row_key="bill", rows_key="bills", party_field="vendor",
            date_field="bill_date", notes_field="debits", figures=BILL_FIGURES,
            not_paid_in_full=not_paid_in_full, control_accounts=payable_accounts,
            settles=PaymentDirection.DISBURSEMENT, sign=-1, control_field="payable_account"))
        register_allocation_model(BillPayment)
        register_payment_check(refuse_held_payment)
        register_void_follow_up(undo_what_it_settled)
        register_bank_movements(challans_paid)
        # A subcontractor's assemblies stand as the batches they were made from (O135, O148).
        register_lot_sources(assembled_from, assemblies_made_from)
        # Quality's verdict may leave a held receipt with nothing held: its to-dos close (review F4).
        register_lot_decided(receipts_quality_released)
        # Who sold the plant a batch: whose certificate of analysis it takes (L28).
        register_lot_supplier(received_from)
