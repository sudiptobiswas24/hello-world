"""
A sack priced on polymer at 100 a kilogramme, 0.11 kg of it a sack,
the index's moves passed on in full beyond 2% of the base.

  T-20  300 sacks at index 100:    no move, nothing.
  T-15  400 sacks at index 106:    6 x 0.11 = 0.66 a sack, 264.00.
  T-10  200 sacks at index 101.50: 1.5 is inside 2, nothing.
  Billed for T-30 to T-1: 264.00 on a supplementary invoice.

  The 400 come back today, with the index at 94: they are varied back
  at the 106 they were charged, -264.00, credited.

  T-5   100 sacks at index 94:     -6 x 0.11 = -0.66 a sack, -66.00:
        credited against the order's last invoice.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Delivery, DeliveryLine, InvoicePolicy, SalesOrder, SalesOrderLine
from .price_variation import (
    PriceIndex,
    PriceIndexValue,
    PriceVariationBill,
    PriceVariationClause,
    bill_variation,
    rows_for,
)
from .tests_base import SalesTestCase

T = timezone.localdate()


def before(days):
    return T - datetime.timedelta(days=days)


class PriceVariationTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        # The clock held at noon on T. T is read once, at import; a run
        # that crossed midnight had the code date a return the next day
        # and these tests failed for the hour after it (found running the
        # suite in reverse, which happened to straddle midnight).
        from unittest.mock import patch

        noon = timezone.make_aware(datetime.datetime.combine(T, datetime.time(12)))
        self.enterContext(patch.object(timezone, "now", return_value=noon))
        from apps.inventory.models import MovementType, StockMovement

        StockMovement.objects.create(item=self.item, warehouse=self.warehouse,
                                     movement_type=MovementType.RECEIPT, uom=self.uom,
                                     quantity=Decimal("1500"), unit_cost=Decimal("4"),
                                     occurred_at=timezone.now())
        self.index = PriceIndex.objects.create(code="PP-RAFFIA", name="PP raffia, ex-works")
        for days, value in ((30, "100"), (17, "106"), (12, "101.5"), (8, "94")):
            PriceIndexValue.objects.create(index=self.index, valid_from=before(days),
                                           value=Decimal(value))
        self.order = SalesOrder.objects.create(customer=self.customer, order_date=before(30),
                                               currency=self.usd,
                                               invoice_policy=InvoicePolicy.DELIVERED)
        self.line = SalesOrderLine.objects.create(
            order=self.order, item=self.item, uom=self.uom, quantity=Decimal("2000"),
            unit_price=Decimal("12"), revenue_account=self.revenue)
        self.order.confirm()
        self.clause = PriceVariationClause.objects.create(
            order_line=self.line, index=self.index, base_value=Decimal("100"),
            polymer_kg_per_unit=Decimal("0.11"), threshold_percent=Decimal("2"))

    def dispatch(self, quantity, days):
        delivery = Delivery.objects.create(sales_order=self.order, delivery_date=before(days))
        DeliveryLine.objects.create(delivery=delivery, order_line=self.line,
                                    warehouse=self.warehouse, quantity_shipped=Decimal(quantity))
        delivery.post()
        return delivery

    def march(self):
        """The three dispatches, and the bill for them."""
        self.dispatch("300", 20)
        self.second = self.dispatch("400", 15)
        self.dispatch("200", 10)
        return bill_variation(self.order, before(30), before(1), self.ar, invoice_date=T)


class VariedDeliveryByDeliveryTests(PriceVariationTestCase):
    def test_each_delivery_at_the_index_on_its_day(self):
        bill = self.march()
        rows = [(row.quantity, row.index_value, row.variation_per_unit, row.amount)
                for row in bill.lines.order_by("delivery__delivery_date")]
        self.assertEqual(rows, [
            (Decimal("300.0000"), Decimal("100.0000"), Decimal("0.000000"), Decimal("0.00")),
            (Decimal("400.0000"), Decimal("106.0000"), Decimal("0.660000"), Decimal("264.00")),
            (Decimal("200.0000"), Decimal("101.5000"), Decimal("0.000000"), Decimal("0.00")),
        ])
        self.assertTrue(bill.number.startswith("PV-"))
        invoice = bill.invoice
        (line,) = invoice.lines.all()
        self.assertEqual((line.quantity, line.unit_price, line.revenue_account, invoice.posted,
                          line.order_line),
                         (Decimal("1"), Decimal("264.00"), self.revenue, False, None))
        self.assertIsNone(bill.credit_note)
        invoice.post()
        self.assertEqual(invoice.total(), Decimal("264.00"))

    def test_a_delivery_is_varied_once(self):
        self.march()
        with self.assertRaisesMessage(ValidationError, "is left to vary"):
            bill_variation(self.order, before(30), before(1), self.ar)
        self.assertEqual(rows_for(self.order, before(30), T), [])

    def test_the_threshold_itself_moves_nothing(self):
        # 2% of 100 is 2: an index of exactly 102 is inside it.
        PriceIndexValue.objects.create(index=self.index, valid_from=before(3),
                                       value=Decimal("102"))
        self.dispatch("100", 2)
        (row,) = rows_for(self.order, before(3), before(1))
        self.assertEqual(row["variation_per_unit"], Decimal("0"))

    def test_only_what_was_dispatched_in_the_window(self):
        self.march()
        self.dispatch("100", 0)
        draft = Delivery.objects.create(sales_order=self.order, delivery_date=before(1))
        DeliveryLine.objects.create(delivery=draft, order_line=self.line,
                                    warehouse=self.warehouse, quantity_shipped=Decimal("10"))
        self.assertEqual(rows_for(self.order, before(30), before(1)), [])
        (row,) = rows_for(self.order, before(30), T)
        self.assertEqual(row["amount"], Decimal("-66.00"))

    def test_nothing_to_bill_when_nothing_moved(self):
        self.dispatch("300", 20)
        bill = bill_variation(self.order, before(30), before(19), self.ar)
        self.assertEqual((bill.total(), bill.invoice, bill.credit_note), (Decimal("0"), None, None))
        self.assertEqual(rows_for(self.order, before(30), before(19)), [])

    def test_taxed_as_the_order_line(self):
        from apps.accounting.models import Account, AccountType, Tax

        payable = Account.objects.create(code="2300", name="GST payable",
                                         account_type=AccountType.LIABILITY)
        gst = Tax.objects.create(code="GST18", name="GST 18%", rate=Decimal("18"),
                                 collected_account=payable, paid_account=payable)
        self.line.taxes.set([gst])
        bill = self.march()
        self.assertEqual(list(bill.invoice.lines.get().taxes.all()), [gst])

    def test_passed_on_in_part(self):
        PriceVariationClause.objects.filter(pk=self.clause.pk).update(
            pass_through_percent=Decimal("50"))
        self.dispatch("400", 15)
        (row,) = rows_for(self.order, before(16), before(14))
        self.assertEqual((row["variation_per_unit"], row["amount"]),
                         (Decimal("0.330000"), Decimal("132.00")))

    def test_a_window_must_run_forwards(self):
        with self.assertRaisesMessage(ValidationError, "runs backwards"):
            rows_for(self.order, T, before(1))


class TheIndexFallsTests(PriceVariationTestCase):
    def test_a_return_goes_back_at_what_it_was_charged(self):
        bill = self.march()
        bill.invoice.post()
        self.second.create_return(credit_invoices=False)
        back = bill_variation(self.order, T, T, self.ar, invoice_date=T)
        (row,) = back.lines.all()
        self.assertEqual((row.quantity, row.index_value, row.amount),
                         (Decimal("-400.0000"), Decimal("106.0000"), Decimal("-264.00")))
        credit = back.credit_note
        self.assertEqual((credit.credits, credit.lines.get().unit_price, back.invoice),
                         (bill.invoice, Decimal("264.00"), None))
        credit.post()

    def test_a_return_at_the_index_frozen_not_one_published_since(self):
        bill = self.march()
        bill.invoice.post()
        # A value back-dated after the bill: the 400 were charged at 106.
        PriceIndexValue.objects.create(index=self.index, valid_from=before(16),
                                       value=Decimal("105"))
        self.second.create_return(credit_invoices=False)
        (row,) = rows_for(self.order, T, T)
        self.assertEqual((row["index_value"], row["amount"]),
                         (Decimal("106.0000"), Decimal("-264.00")))

    def test_a_return_before_its_delivery_was_billed(self):
        self.dispatch("300", 20)
        self.dispatch("400", 15).create_return(credit_invoices=False)
        amounts = sorted(row["amount"] for row in rows_for(self.order, before(30), T))
        self.assertEqual(amounts, [Decimal("-264.00"), Decimal("0.00"), Decimal("264.00")])

    def test_a_fall_is_credited_against_the_last_invoice(self):
        self.dispatch("100", 5)
        with self.assertRaisesMessage(ValidationError, "nothing on"):
            bill_variation(self.order, before(6), before(4), self.ar)
        self.assertFalse(PriceVariationBill.objects.exists())
        first = self.order.create_invoice(self.ar, invoice_date=before(4))
        first.post()
        self.dispatch("50", 5)
        invoice = self.order.create_invoice(self.ar, invoice_date=before(3))
        invoice.post()
        bill = bill_variation(self.order, before(6), before(4), self.ar)
        # 150 sacks at -0.66, against the later of the two invoices.
        self.assertEqual((bill.credit_note.credits, bill.credit_note.lines.get().unit_price),
                         (invoice, Decimal("99.00")))


class CancelledAndKeptTests(PriceVariationTestCase):
    def test_cancelled_while_a_draft_frees_its_deliveries(self):
        bill = self.march()
        draft = bill.invoice
        bill.cancel()
        self.assertFalse(type(draft).objects.filter(pk=draft.pk).exists())
        self.assertEqual(len(rows_for(self.order, before(30), before(1))), 3)
        with self.assertRaisesMessage(ValidationError, "already cancelled"):
            bill.cancel()
        again = bill_variation(self.order, before(30), before(1), self.ar)
        self.assertEqual(again.total(), Decimal("264.00"))

    def test_not_once_issued(self):
        bill = self.march()
        bill.invoice.post()
        with self.assertRaisesMessage(ValidationError, "has been issued"):
            bill.cancel()

    def test_the_terms_and_the_index_as_billed(self):
        self.march()
        self.clause.base_value = Decimal("90")
        with self.assertRaisesMessage(ValidationError, "its terms are as agreed"):
            self.clause.save()
        with self.assertRaisesMessage(ValidationError, "it stays"):
            self.clause.delete()
        value = PriceIndexValue.objects.get(valid_from=before(17))
        with self.assertRaisesMessage(ValidationError, "enter a new one"):
            value.save()
        with self.assertRaisesMessage(ValidationError, "has been billed on"):
            value.delete()
        unused = PriceIndexValue.objects.create(
            index=self.index, valid_from=T + datetime.timedelta(days=5), value=Decimal("99"))
        unused.delete()

    def test_an_index_with_nothing_published_yet(self):
        with self.assertRaisesMessage(ValidationError, "has no value published"):
            self.index.value_on(before(40))


class PriceVariationApiTests(PriceVariationTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("commercial"))

    def test_published_previewed_billed_and_cancelled(self):
        base = f"/api/sales/price-indices/{self.index.pk}/values/"
        response = self.client.post(base, {"valid_from": str(T + datetime.timedelta(days=3)),
                                           "value": "97"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        for bad in ({"valid_from": "soon", "value": "97"}, {"valid_from": str(T),
                                                           "value": "NaN"},
                    {"valid_from": str(T), "value": "0"}):
            self.assertEqual(self.client.post(base, bad, format="json").status_code, 400)
        self.dispatch("400", 15)
        response = self.client.get("/api/sales/price-variation-bills/preview/", {
            "order": self.order.pk, "start": str(before(30)), "end": str(before(1))})
        self.assertEqual([row["amount"] for row in response.json()], ["264.00"])
        response = self.client.post("/api/sales/price-variation-bills/", {
            "order": self.order.pk, "start": str(before(30)), "end": str(before(1)),
            "receivable_account": self.ar.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["total"], body["lines"][0]["index_value"]),
                         ("264.00", "106.0000"))
        again = self.client.post("/api/sales/price-variation-bills/", {
            "order": self.order.pk, "start": str(before(30)), "end": str(before(1)),
            "receivable_account": self.ar.pk}, format="json")
        self.assertEqual(again.status_code, 400)
        response = self.client.post(f"/api/sales/price-variation-bills/{body['id']}/cancel/")
        self.assertEqual((response.status_code, response.json()["cancelled"]), (200, True))
        self.assertEqual(self.client.get("/api/sales/price-variation-bills/preview/", {
            "order": self.order.pk}).status_code, 400)


class SignTests(PriceVariationTestCase):
    def test_the_amount_is_signed_as_the_quantity_times_the_rate(self):
        from django.db import IntegrityError, transaction

        from .price_variation import PriceVariationLine

        bill = self.march()
        row = bill.lines.get(amount=Decimal("264.00"))
        for quantity, amount in (("-400", "264.00"), ("0", "0"), ("400", "-264.00")):
            with self.assertRaises(IntegrityError), transaction.atomic():
                PriceVariationLine.objects.filter(pk=row.pk).update(
                    quantity=Decimal(quantity), amount=Decimal(amount))
