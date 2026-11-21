"""
O458, shared rule B on sales' documents known by their status: a line is changed, added or removed only while both
the document it leaves and the one it joins take changes, read from the database once held. The mirror of
purchasing's (apps/purchasing/tests_lines_answer.py): a cancelled order took lines; a quotation's lines were edited
once it was declined or expired, rewriting what the customer was told; and what a job-work customer supplies was
moved off a confirmed order, or changed on one read while it was still a draft.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import Quotation, QuotationLine, SalesOrder, SalesOrderLine, SuppliedItem
from .tests_base import SalesTestCase


class CancelledOrderTakesNoLineTests(SalesTestCase):
    def cancelled(self):
        order = self.make_order()
        order.cancel()
        return SalesOrder.objects.get(pk=order.pk)

    def draft(self, job_work=False):
        return SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1),
                                         currency=self.usd, is_job_work=job_work)

    def test_a_cancelled_order_takes_no_new_line(self):
        order = self.cancelled()
        with self.assertRaisesMessage(ValidationError, f"{order} is cancelled"):
            SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
                                          unit_price=Decimal("100"), revenue_account=self.revenue)

    def test_a_cancelled_orders_line_is_neither_deleted_nor_moved_onto_a_draft(self):
        order = self.cancelled()
        line = order.lines.get()
        with self.assertRaisesMessage(ValidationError, f"{order} is cancelled"):
            line.delete()
        line.order = self.draft()
        with self.assertRaisesMessage(ValidationError, f"{order} is cancelled"):
            line.save()
        self.assertEqual(order.lines.count(), 1)

    def test_what_a_customer_supplies_is_not_moved_off_a_confirmed_order_nor_changed_on_one_read_as_a_draft(self):
        order = self.draft(job_work=True)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
                                      unit_price=Decimal("100"), revenue_account=self.revenue)
        supplied = SuppliedItem.objects.create(order=order, item=self.item)
        supplied.order  # read while the order is a draft
        SalesOrder.objects.get(pk=order.pk).confirm()
        with self.assertRaisesMessage(ValidationError, "is confirmed; what the customer supplies is settled"):
            supplied.save()
        supplied.order = self.draft(job_work=True)
        with self.assertRaisesMessage(ValidationError, "is confirmed; what the customer supplies is settled"):
            supplied.save()
        self.assertEqual(SuppliedItem.objects.filter(order=order).count(), 1)


class QuotationLinesChangeOnlyOnADraftTests(SalesTestCase):
    def quotation(self):
        quotation = Quotation.objects.create(customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
                                             currency=self.usd)
        QuotationLine.objects.create(quotation=quotation, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                     unit_price=Decimal("10"), revenue_account=self.revenue)
        return quotation

    def test_a_declined_quotations_line_is_not_repriced(self):
        quotation = self.quotation()
        quotation.mark_sent()
        quotation.decline()
        line = quotation.lines.get()
        line.unit_price = Decimal("8")
        with self.assertRaisesMessage(ValidationError, "revise it instead of editing it"):
            line.save()
        self.assertEqual(QuotationLine.objects.get(pk=line.pk).unit_price, Decimal("10"))

    def test_a_line_is_not_moved_off_a_quotation_sent_onto_a_draft(self):
        sent = self.quotation()
        sent.mark_sent()
        line = sent.lines.get()
        line.quotation = self.quotation()
        with self.assertRaisesMessage(ValidationError, "revise it instead of editing it"):
            line.save()
        self.assertEqual(sent.lines.count(), 1)

    def test_a_drafts_line_is_still_edited(self):
        line = self.quotation().lines.get()
        line.unit_price = Decimal("9")
        line.save()
        self.assertEqual(QuotationLine.objects.get(pk=line.pk).unit_price, Decimal("9"))
