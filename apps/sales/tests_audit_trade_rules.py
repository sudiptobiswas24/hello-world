"""
The two shared trade rules, as far as audit_invariants can see them.

Rule B, a line answers to its document: a line of a posted document that
its own route writes calls answer_to_its_document() in save() and delete().
A save() that asks only the document it joins (the shape of O82) and a line
with no delete() at all (O66) are reported; both calls are not; a reported
line that answers now is a stale exemption.

Rule A, frozen once it moved: a model declaring FROZEN_ONCE_MOVED covers its
item, unit, party, currency, price and discount and the document it is a
line of, and asks refuse_changing_what_moved() in save(); an order line a
posted document's line names declares it.
"""

from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command
from .models import SalesOrderLine

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


def frozen(app="sales", text=None):
    from apps.core.management.commands.audit_invariants import app_sources

    sources = app_sources()
    if text is not None:
        sources[app] = {Path(f"apps/{app}/example.py"): text}
    return [detail for _shape, detail in Command().frozen_fields_uncovered([app], sources)]


class WhatMovedStaysAsItMovedTests(SimpleTestCase):
    def test_this_checkout_is_covered(self):
        self.assertEqual(frozen("sales") + frozen("purchasing"), [])

    def test_a_declaration_that_leaves_the_item_out_is_reported(self):
        declared = dict(SalesOrderLine.FROZEN_ONCE_MOVED)
        del declared["item"]
        with mock.patch.object(SalesOrderLine, "FROZEN_ONCE_MOVED", declared):
            self.assertEqual(frozen(), ["sales.SalesOrderLine.item can still change once things have moved "
                                        "against it: FROZEN_ONCE_MOVED leaves it out."])

    def test_a_save_that_never_asks_is_reported(self):
        found = frozen(text="class SalesOrderLine(TaxedLineMixin, AuditModel):\n"
                            "    def save(self, *args, **kwargs):\n"
                            "        super().save(*args, **kwargs)\n")
        self.assertIn("sales.SalesOrderLine declares FROZEN_ONCE_MOVED and its save() never asks "
                      "refuse_changing_what_moved().", found)

    def test_an_order_line_a_posted_line_names_declares_it(self):
        with mock.patch.object(SalesOrderLine, "FROZEN_ONCE_MOVED", None):
            self.assertEqual(frozen(), ["sales.SalesOrderLine is named as the order line of a posted document's "
                                        "line and declares no FROZEN_ONCE_MOVED."])
