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
