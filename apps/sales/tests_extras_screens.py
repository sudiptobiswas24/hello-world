"""
The sales extras' screens, server side, asked as the people who use
them: rep A carries Acme, rep B carries Beta, and the AR Manager sees
both (the rep fixture).

Accounts keep the price lists and the polymer index; a rep reads them.
A rep books their own customer's call-offs, index clauses and supplied
material, and asks nothing of another rep's orders: not a list row, and
not the preview of a price-variation bill, which looked an order up by
its number alone.
"""

import datetime
from decimal import Decimal

from .models import SalesOrderLine
from .price_variation import PriceIndex, PriceIndexValue, PriceVariationClause
from .tests_reps import RepTestCase


class ExtrasTestCase(RepTestCase):
    def setUp(self):
        super().setUp()
        self.ar = self.as_role("AR Manager")
        self.acme_line = SalesOrderLine.objects.create(order=self.acme_order, item=self.item, uom=self.uom,
                                                       quantity=Decimal("1000"), unit_price=Decimal("10"))
        self.beta_line = SalesOrderLine.objects.create(order=self.beta_order, item=self.item, uom=self.uom,
                                                       quantity=Decimal("500"), unit_price=Decimal("10"))


class PriceListTests(ExtrasTestCase):
    def test_accounts_keep_a_list_and_its_prices_and_a_rep_reads_them(self):
        made = self.ar.post("/api/sales/price-lists/", {"code": "STD", "name": "Standard"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        price = self.ar.post("/api/sales/price-list-items/", {"price_list": made.json()["id"], "item": self.item.pk,
                                                              "min_quantity": "100", "unit_price": "9.50"}, format="json")
        self.assertEqual(price.status_code, 201, price.content)
        rep = self.as_user(self.rep_a)
        [row] = rep.get("/api/sales/price-list-items/", {"price_list": made.json()["id"]}).json()
        self.assertEqual((row["item_label"], row["unit_price"]), ("WIDGET-1 · Widget", "9.50"))
        self.assertEqual(rep.post("/api/sales/price-list-items/", {"price_list": made.json()["id"], "item": self.item.pk,
                                                                   "unit_price": "1"}, format="json").status_code, 403)


class PriceIndexTests(ExtrasTestCase):
    def test_accounts_publish_a_value_and_whoever_quotes_reads_it(self):
        index = PriceIndex.objects.create(code="PP-RIL", name="Reliance PP raffia")
        url = f"/api/sales/price-indices/{index.pk}/values/"
        published = self.ar.post(url, {"valid_from": "2026-09-01", "value": "104.50"}, format="json")
        self.assertEqual(published.status_code, 201, published.content)
        rep = self.as_user(self.rep_a)
        read = rep.get(url)
        self.assertEqual(read.status_code, 200, read.content)
        self.assertEqual(read.json(), [{"valid_from": "2026-09-01", "value": "104.5000"}])
        self.assertEqual(rep.post(url, {"valid_from": "2026-10-01", "value": "99"}, format="json").status_code, 403)
        self.assertEqual(PriceIndexValue.objects.filter(index=index).count(), 1)


class CallOffTests(ExtrasTestCase):
    def test_a_rep_books_their_customers_schedule_named_by_order_and_line(self):
        rep = self.as_user(self.rep_a)
        lines = rep.get("/api/sales/sales-order-lines/", {"order": self.acme_order.pk}).json()
        self.assertEqual([found["id"] for found in lines], [self.acme_line.pk])
        made = rep.post("/api/sales/call-offs/", {"line": self.acme_line.pk, "due_on": "2026-10-15",
                                                  "quantity": "300", "reference": "REL-7"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = rep.get("/api/sales/call-offs/", {"line__order": self.acme_order.pk}).json()
        self.assertEqual(row["line_label"], f"{self.acme_order.number} · Acme Co · {self.acme_line.label()}")
        self.assertEqual(rep.get("/api/sales/call-offs/", {"from": "2026-10-16"}).json(), [])

    def test_and_none_on_another_reps_customer(self):
        response = self.as_user(self.rep_a).post("/api/sales/call-offs/", {
            "line": self.beta_line.pk, "due_on": "2026-10-15", "quantity": "100"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Not one of your customers", response.content.decode())


class PriceVariationTests(ExtrasTestCase):
    def setUp(self):
        super().setUp()
        self.index = PriceIndex.objects.create(code="PP-RIL", name="Reliance PP raffia")
        PriceVariationClause.objects.create(order_line=self.beta_line, index=self.index, base_value=Decimal("100"),
                                            polymer_kg_per_unit=Decimal("0.085"))
        # A rep given the bill's rights, so what stops them is the scope, not the role.
        from django.contrib.auth.models import Permission

        self.rep_a.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="sales", codename__in=["view_pricevariationbill", "add_pricevariationbill"]))

    def test_a_clause_is_named_by_its_line_and_index(self):
        made = self.ar.post("/api/sales/price-clauses/", {"order_line": self.acme_line.pk, "index": self.index.pk,
                                                          "base_value": "100", "polymer_kg_per_unit": "0.085"},
                            format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = self.ar.get("/api/sales/price-clauses/", {"order_line__order": self.acme_order.pk}).json()
        self.assertEqual(row["index_code"], "PP-RIL")

    def test_another_reps_order_cannot_be_previewed_or_billed(self):
        rep = self.as_user(self.rep_a)
        preview = rep.get("/api/sales/price-variation-bills/preview/", {
            "order": self.beta_order.pk, "start": "2026-09-01", "end": "2026-09-30"})
        self.assertEqual(preview.status_code, 404, preview.content)
        PriceVariationClause.objects.create(order_line=self.acme_line, index=self.index, base_value=Decimal("100"),
                                            polymer_kg_per_unit=Decimal("0.085"))
        own = rep.get("/api/sales/price-variation-bills/preview/", {
            "order": self.acme_order.pk, "start": "2026-09-01", "end": "2026-09-30"})
        self.assertNotEqual(own.status_code, 404, own.content)
        bill = rep.post("/api/sales/price-variation-bills/", {
            "order": self.beta_order.pk, "start": "2026-09-01", "end": "2026-09-30",
            "receivable_account": self.receivable.pk}, format="json")
        self.assertEqual(bill.status_code, 404, bill.content)


class RemindersAndRecurringTests(ExtrasTestCase):
    def test_the_lists_name_what_they_are_about(self):
        made = self.ar.post("/api/sales/recurring-invoices/", {
            "code": "RENT", "customer": self.acme.pk, "receivable_account": self.receivable.pk,
            "interval": "monthly", "start_date": "2026-10-01"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = self.ar.get("/api/sales/recurring-invoices/", {"customer": self.acme.pk, "is_active": "true"}).json()
        self.assertEqual((row["customer_name"], row["next_run_date"]), ("Acme Co", "2026-10-01"))
        self.assertEqual(self.ar.get("/api/sales/dunning-notices/", {"invoice__customer": self.acme.pk}).status_code, 200)

    def test_the_commission_report_takes_its_span(self):
        response = self.ar.get("/api/sales/commission-plans/report/", {"from": "2026-09-01", "to": "2026-09-30"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), [])

    def test_a_rep_runs_no_reminders(self):
        self.assertEqual(self.as_user(self.rep_a).post("/api/sales/dunning-levels/run/", {"send": False},
                                                       format="json").status_code, 403)
        today = datetime.date(2026, 10, 6)
        self.assertEqual(self.ar.post("/api/sales/dunning-levels/run/", {"send": False, "as_of": str(today)},
                                      format="json").status_code, 200)
