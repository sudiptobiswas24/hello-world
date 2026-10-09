"""
Shared rule B, as far as audit_invariants can see it.

Rule B, a line answers to its document: a line of a posted document that
its own route writes calls answer_to_its_document() in save() and delete().
A save() that asks only the document it joins (the shape of O82) and a line
with no delete() at all (O66) are reported; both calls are not; a reported
line that answers now is a stale exemption.
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command

BOTH = (
    "    def save(self, *args, **kwargs):\n"
    "        answer_to_its_document(self, 'delivery', 'posted')\n"
    "        super().save(*args, **kwargs)\n"
    "    def delete(self, *args, **kwargs):\n"
    "        answer_to_its_document(self, 'delivery', 'posted')\n"
    "        return super().delete(*args, **kwargs)\n"
)


def lines(app, cls, body):
    found = Command().lines_not_answering_to_their_document(
        [app], {app: {Path(f"apps/{app}/example.py"): f"class {cls}(AuditModel):\n" + body}})
    return [shape for shape, detail in found if f"{app}.{cls} " in detail]


class ALineAnswersToItsDocumentTests(SimpleTestCase):
    def test_a_save_that_asks_only_where_it_goes_is_reported(self):
        self.assertEqual(lines("sales", "DeliveryLine", (
            "    def save(self, *args, **kwargs):\n"
            "        if Delivery.objects.filter(pk=self.delivery_id, posted=True).exists():\n"
            "            raise ValidationError('posted')\n"
            "        super().save(*args, **kwargs)\n"
        )), ["line not answering to its document"])

    def test_a_line_with_both_is_not(self):
        self.assertEqual(lines("sales", "DeliveryLine", BOTH), [])

    def test_a_reported_line_that_answers_now_is_a_stale_exemption(self):
        self.assertEqual(lines("inventory", "StockAdjustmentLine", BOTH), ["stale exemption"])

    def test_every_line_this_checkout_writes_answers_or_is_reported(self):
        from apps.core.management.commands.audit_invariants import app_sources

        self.assertEqual(Command().lines_not_answering_to_their_document(
            ["sales", "purchasing", "quality", "manufacturing"], app_sources()), [])
