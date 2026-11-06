"""
O401: an order, invoice or bill was made in two steps, the header first and then each line, so whoever stopped
between them left a half-made draft behind. A new document now takes its lines with it (`new_lines`), and the
header and every line are written in the one savepoint the viewset opens: a refusal on any line makes nothing.

Through the API, as the people who do it, on the demo plant. Numbers, worked in /home/user/work/calc_fix_ui/o401.py:
  20,000 rice sacks x 11.20 = 2,24,000.00; CGST 9% + SGST 9% 40,320.00; 2,64,320.00.
  100 x 11.20 = 1,120.00 naming no tax on purpose ([]): the order 2,65,440.00.
  Across a state line (Godavari, Andhra Pradesh): IGST 18% 40,320.00, the same 2,64,320.00.
  5,000 kg PP raffia x 112.00 = 5,60,000.00; CGST + SGST 1,00,800.00; 6,60,800.00.
"""

from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import Tax
from apps.core.models import Party
from apps.inventory.models import Item
from apps.purchasing.models import Bill, BillLine, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine
from apps.sales.models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine
from apps.web.tests_load_demo import load

D = Decimal
User = get_user_model()
TODAY = lambda: str(timezone.localdate())  # noqa: E731


class LinesInOneSaveTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def as_(self, username):
        client = APIClient()
        client.force_authenticate(User.objects.get(username=username))
        return client

    def item(self, sku):
        return Item.objects.get(sku=sku).pk

    def codes(self, ids):
        return sorted(Tax.objects.filter(pk__in=ids).values_list("code", flat=True))

    def counts(self):
        return [model.objects.count() for model in
                (SalesOrder, SalesOrderLine, Invoice, InvoiceLine, PurchaseOrder, PurchaseOrderLine, Bill, BillLine)]

    def unreceived(self):
        # A bill line for an item nothing has come in of: one received and unbilled is refused, rightly, for
        # naming no order line.
        return Item.objects.exclude(pk__in=GoodsReceiptLine.objects.values("order_line__item")) \
            .filter(item_type="goods", is_active=True).order_by("pk").first()


class AnOrderIsMadeWithItsLines(LinesInOneSaveTestCase):
    def test_a_rep_makes_an_order_with_two_lines_in_one_save(self):
        imran = self.as_("imran")
        made = imran.post("/api/sales/sales-orders/", {
            "customer": Party.objects.get(code="C-KONK").pk, "order_date": TODAY(),
            "new_lines": [
                {"item": self.item("SACK-RICE25"), "quantity": "20000", "unit_price": "11.20"},
                {"item": self.item("SACK-RICE25"), "quantity": "100", "unit_price": "11.20", "taxes": []},
            ]}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        body = made.json()
        self.assertNotIn("new_lines", body)
        first, second = body["lines"]
        # No taxes named: the item's (O459). Named as none: none.
        self.assertEqual(self.codes(first["taxes"]), ["CGST9", "SGST9"])
        self.assertEqual(first["total"], "264320.00")
        self.assertEqual(second["taxes"], [])
        self.assertEqual(second["total"], "1120.00")
        self.assertEqual(body["total"], "265440.00")
        order = SalesOrder.objects.get(pk=body["id"])
        self.assertEqual({line.created_by.username for line in order.lines.all()}, {"imran"})

    def test_across_a_state_line_the_lines_take_the_documents_position(self):
        # O446: the order settles which position its taxes are taken under as it is saved; its lines follow it.
        rohit = self.as_("rohit")
        customer = Party.objects.get(code="C-GODA").pk
        made = rohit.post("/api/sales/sales-orders/", {"customer": customer, "order_date": TODAY(), "new_lines": [
            {"item": self.item("SACK-RICE25"), "quantity": "20000", "unit_price": "11.20"}]}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        order = SalesOrder.objects.get(pk=made.json()["id"])
        self.assertEqual([tax.code for tax in order.lines.get().effective_taxes()], ["IGST18"])
        self.assertEqual(order.total(), D("264320.00"))
        # The same as made in two steps.
        header = rohit.post("/api/sales/sales-orders/", {"customer": customer, "order_date": TODAY()}, format="json")
        rohit.post("/api/sales/sales-order-lines/", {"order": header.json()["id"], "item": self.item("SACK-RICE25"),
                                                     "quantity": "20000", "unit_price": "11.20"}, format="json")
        twice = SalesOrder.objects.get(pk=header.json()["id"])
        self.assertEqual(order.taxes_taken_under_id, twice.taxes_taken_under_id)
        self.assertEqual(order.total(), twice.total())

    def test_a_purchase_order_is_made_with_its_line(self):
        made = self.as_("ravi").post("/api/purchasing/purchase-orders/", {
            "vendor": Party.objects.get(code="V-GRAN").pk, "order_date": TODAY(),
            "new_lines": [{"item": self.item("PP-RAFFIA"), "quantity": "5000", "unit_price": "112.00"}]}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        line, = made.json()["lines"]
        self.assertEqual(self.codes(line["taxes"]), ["CGST9", "SGST9"])
        self.assertEqual(made.json()["total"], "660800.00")

    def test_an_invoice_and_a_bill_are_made_with_their_lines_as_in_two_steps(self):
        rohit, ravi = self.as_("rohit"), self.as_("ravi")
        customer, vendor = Party.objects.get(code="C-KONK").pk, Party.objects.get(code="V-GRAN").pk
        sack, other = self.item("SACK-RICE25"), self.unreceived().pk
        cases = [
            (rohit, "/api/sales/invoices/", "/api/sales/invoice-lines/", "invoice",
             {"customer": customer, "invoice_date": TODAY()}, {"item": sack, "quantity": "10", "unit_price": "11.20"}),
            (ravi, "/api/purchasing/bills/", "/api/purchasing/bill-lines/", "bill",
             {"vendor": vendor, "bill_date": TODAY(), "reference": "GR/1"},
             {"item": other, "quantity": "50", "unit_price": "112.00"}),
        ]
        for client, documents, lines, parent, header, line in cases:
            with self.subTest(documents):
                once = client.post(documents, {**header, "new_lines": [line]}, format="json")
                self.assertEqual(once.status_code, 201, once.content)
                made = client.post(documents, {**header, "reference": "GR/2"} if parent == "bill" else header,
                                   format="json")
                self.assertEqual(made.status_code, 201, made.content)
                added = client.post(lines, {**line, parent: made.json()["id"]}, format="json")
                self.assertEqual(added.status_code, 201, added.content)
                one, = once.json()["lines"]
                self.assertEqual((sorted(one["taxes"]), one["total"]), (sorted(added.json()["taxes"]), added.json()["total"]))
                self.assertTrue(one["taxes"])
                self.assertEqual(once.json()["taxes_taken_under"], made.json()["taxes_taken_under"])


class ARefusedLineMakesNothing(LinesInOneSaveTestCase):
    def test_a_refused_line_leaves_no_half_made_document(self):
        before = self.counts()
        cases = [
            (self.as_("imran"), "/api/sales/sales-orders/", {"customer": Party.objects.get(code="C-KONK").pk,
                                                              "order_date": TODAY()}, "SACK-RICE25"),
            (self.as_("rohit"), "/api/sales/invoices/", {"customer": Party.objects.get(code="C-KONK").pk,
                                                          "invoice_date": TODAY()}, "SACK-RICE25"),
            (self.as_("ravi"), "/api/purchasing/purchase-orders/", {"vendor": Party.objects.get(code="V-GRAN").pk,
                                                                     "order_date": TODAY()}, "PP-RAFFIA"),
            (self.as_("ravi"), "/api/purchasing/bills/", {"vendor": Party.objects.get(code="V-GRAN").pk,
                                                          "bill_date": TODAY(), "reference": "GR/9"}, None),
        ]
        for client, documents, header, sku in cases:
            with self.subTest(documents):
                good = self.item(sku) if sku else self.unreceived().pk
                refused = client.post(documents, {**header, "new_lines": [
                    {"item": good, "quantity": "10", "unit_price": "11.20"},
                    {"item": good, "quantity": "not a number"},
                ]}, format="json")
                self.assertEqual(refused.status_code, 400, refused.content)
                said = refused.json()["new_lines"]
                self.assertEqual(said[0], {})
                self.assertIn("quantity", said[1])
                self.assertEqual(self.counts(), before)

    def test_a_line_the_model_refuses_makes_nothing_either(self):
        # Refused in save(), not by the serializer: a line that is neither an item, a charge nor described.
        before = self.counts()
        refused = self.as_("imran").post("/api/sales/sales-orders/", {
            "customer": Party.objects.get(code="C-KONK").pk, "order_date": TODAY(),
            "new_lines": [{"item": self.item("SACK-RICE25"), "quantity": "10", "unit_price": "11.20"},
                          {"quantity": "10", "unit_price": "11.20"}]}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(self.counts(), before)

    def test_lines_need_the_line_s_own_permission(self):
        # The sales manager's, but for adding an order's lines.
        user = User.objects.create_user("header-only")
        user.user_permissions.add(*Permission.objects.filter(group__user__username="rohit")
                                  .exclude(codename="add_salesorderline").distinct())
        self.assertTrue(user.has_perm("sales.add_salesorder"))
        client = APIClient()
        client.force_authenticate(user)
        before = self.counts()
        refused = client.post("/api/sales/sales-orders/", {
            "customer": Party.objects.get(code="C-KONK").pk, "order_date": TODAY(),
            "new_lines": [{"item": self.item("SACK-RICE25"), "quantity": "10", "unit_price": "11.20"}]}, format="json")
        self.assertEqual(refused.status_code, 403, refused.content)
        self.assertEqual(self.counts(), before)

    def test_a_saved_document_s_lines_are_not_sent_with_its_header(self):
        imran = self.as_("imran")
        made = imran.post("/api/sales/sales-orders/", {"customer": Party.objects.get(code="C-KONK").pk,
                                                        "order_date": TODAY()}, format="json")
        refused = imran.patch(f"/api/sales/sales-orders/{made.json()['id']}/", {
            "new_lines": [{"item": self.item("SACK-RICE25"), "quantity": "10"}]}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(SalesOrderLine.objects.filter(order_id=made.json()["id"]).count(), 0)
