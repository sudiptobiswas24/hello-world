from django.apps import AppConfig


class GstConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.gst"
    verbose_name = "GST returns"

    def ready(self):
        from apps.manufacturing.jobwork import register_challan_void_guard
        from apps.sales.models import register_invoice_stamp

        from .einvoice import invoice_stamp
        from .ewaybill import refuse_challan_void
        from .gstr2b import refuse_supplier_note_change
        from apps.purchasing.models import register_supplier_note_guard

        register_invoice_stamp(invoice_stamp)
        register_challan_void_guard(refuse_challan_void)
        register_supplier_note_guard(refuse_supplier_note_change)

        from apps.accounting.models import register_bank_movements, register_void_follow_up
        from apps.purchasing.models import register_debit_note_follow_up

        from .reverse_charge import cancel_vouchers_of, cancel_with_a_whole_debit_note, challans_paid

        # Reverse charge: a whole debit note cancels its self-invoice, a void its payment voucher, and the
        # GST challans' money through the bank is matched as a TDS challan's is.
        register_debit_note_follow_up(cancel_with_a_whole_debit_note)
        register_void_follow_up(cancel_vouchers_of)
        register_bank_movements(challans_paid)
