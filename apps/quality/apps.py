from django.apps import AppConfig


class QualityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.quality"
    label = "quality"

    def ready(self):
        from apps.inventory.tracking import register_lot_hold, register_lot_page_question, register_lot_question

        from .release import one_question, prefill_page, why_held

        # A pick that chooses batches for itself asks the release gate
        # too, so it never chooses one a person could not have named.
        register_lot_hold(why_held)
        # Several asks of one stock list share what each batch's lineage says (review F7).
        register_lot_question(one_question)
        # A page of batches is asked once for the page (O231).
        register_lot_page_question(prefill_page)
