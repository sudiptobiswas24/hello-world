"""review_stat2 probes: O160 objection / acceptance, through the API as roles."""
import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.purchasing.models import (Bill, BillLine, GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine)
from apps.purchasing.msme import msme_bills
from apps.purchasing.tests_msme import JUNE, MsmeTestCase

d = datetime.date


def show(*a):
    print("RV2", *a)


class Rv2(MsmeTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def api(self, role=None):
        c = APIClient()
        if role:
            u = User.objects.create_user(role.replace(" ", "").lower() + str(User.objects.count()))
            u.groups.add(Group.objects.get(name=role))
            c.force_authenticate(u)
        return c

    def order(self, qty="10"):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JUNE)
        line = PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D(qty), unit_price=D("100"))
        order.confirm()
        return order, line

    def receipt(self, order, line, day, qty):
        r = GoodsReceipt.objects.create(purchase_order=order, receipt_date=day)
        GoodsReceiptLine.objects.create(receipt=r, order_line=line, warehouse=self.warehouse, quantity_received=D(qty))
        r.post()
        return r

    def billfor(self, order, line, day, qty, terms=None):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=day, payable_account=self.payable,
                                   payment_terms=terms or self.net60, purchase_order=order)
        BillLine.objects.create(bill=bill, order_line=line, item=self.item, quantity=D(qty), unit_price=D("100"),
                                expense_account=self.expense)
        bill.post()
        return Bill.objects.get(pk=bill.pk)

    def fresh(self, b):
        return Bill.objects.get(pk=b.pk)

    def rows(self, b):
        return [(str(r["due_date"]), str(r["amount"])) for r in self.fresh(b).installments()]

    def test_flow_and_roles(self):
        order, line = self.order()
        r = self.receipt(order, line, d(2026, 6, 10), "10")
        bill = self.billfor(order, line, d(2026, 6, 25), "10")
        url = f"/api/purchasing/goods-receipts/{r.pk}/objection/"
        show("A0 pay_by", self.fresh(bill).pay_by(), self.rows(bill))
        for role in ("Warehouse Staff", "AP Manager", "Purchasing Clerk", "Quality Inspector", "Controller", "GST Officer", "Bookkeeper"):
            resp = self.api(role).post(url, {"objected_on": "2026-06-30", "objection": "x"}, format="json")  # too late: 400 for allowed, 403 for refused
            show("A1 role", role, "->", resp.status_code)
        c = self.api("Warehouse Staff")
        resp = c.post(url, {"objected_on": "2026-06-12", "objection": "short count"}, format="json")
        show("A2 record", resp.status_code, resp.json().get("objected_on"), "| pay_by", self.fresh(bill).pay_by(), self.rows(bill))
        resp = c.post(url, {"objection_removed_on": "2026-06-20"}, format="json")
        show("A3 removed 20 Jun", resp.status_code, "| pay_by", self.fresh(bill).pay_by(), self.rows(bill))
        resp = c.delete(url)
        show("A4 withdraw (after removal)", resp.status_code, "| pay_by", self.fresh(bill).pay_by(), self.rows(bill))
        # inputs
        for body in ({"objected_on": "notadate", "objection": "x"}, {"objected_on": 20260612, "objection": "x"},
                     {"objected_on": "2026-06-12", "objection": "y" * 300}, {"objected_on": "2026-06-12", "objection": ["a"]},
                     {"objected_on": "2026-06-09", "objection": "x"}, {"objected_on": "2026-06-25", "objection": "x"},
                     {"objected_on": "2026-06-26", "objection": "x"}, {"objected_on": "2026-06-12", "objection": "  "}, {}):
            try:
                resp = c.post(url, body, format="json")
                show("A5 body", str(body)[:60], "->", resp.status_code, str(resp.content)[:90])
                if resp.status_code == 200:
                    c.delete(url)
            except Exception as e:  # noqa
                show("A5 body", str(body)[:60], "-> EXC", type(e).__name__, str(e)[:80])
        resp = c.post(url, {"objected_on": "2026-06-12", "objection": "again"}, format="json")
        show("A6 record again ->", resp.status_code)
        resp = c.post(url, {"objected_on": "2026-06-12", "objection": "dup"}, format="json")
        show("A6 record dup ->", resp.status_code, str(resp.content)[:90])
        resp = c.post(url, {"objection_removed_on": "2026-06-11"}, format="json")
        show("A7 removal before objection ->", resp.status_code)
        resp = c.post(url, {"objection_removed_on": "2099-01-01"}, format="json")
        show("A7 removal in future ->", resp.status_code)
        resp = c.patch(f"/api/purchasing/goods-receipts/{r.pk}/", {"objected_on": "2026-06-12"}, format="json")
        show("A8 PATCH receipt ->", resp.status_code, str(resp.content)[:80])
        resp = self.api().post(url, {"objected_on": "2026-06-12", "objection": "x"}, format="json")
        show("A9 anonymous ->", resp.status_code)

    def test_two_deliveries_one_bill(self):
        order, line = self.order()
        r1 = self.receipt(order, line, d(2026, 6, 1), "6")
        r2 = self.receipt(order, line, d(2026, 6, 20), "4")
        bill = self.billfor(order, line, d(2026, 6, 25), "10")
        show("B0 pay_by", bill.pay_by(), "inst", self.rows(bill), "total", bill.total())
        row = [x for x in msme_bills(JUNE, d(2027, 3, 31), as_of=d(2026, 8, 10)) if x["bill"] == bill.pk][0]
        show("B0 report as of 10 Aug (unpaid):", {k: str(v) for k, v in row.items() if k in ("due", "days_late", "at_risk", "unpaid")})
        # pay in full on 10 Aug
        self.pay(bill, "1000", d(2026, 8, 10))
        row = [x for x in msme_bills(JUNE, d(2027, 3, 31), as_of=d(2026, 8, 10)) if x["bill"] == bill.pk][0]
        show("B1 paid 10 Aug in full:", {k: str(v) for k, v in row.items() if k in ("due", "paid_on", "days_late", "at_risk", "unpaid")})
        # object to lot 2 only
        c = self.api("Warehouse Staff")
        resp = c.post(f"/api/purchasing/goods-receipts/{r2.pk}/objection/", {"objected_on": "2026-06-22", "objection": "bad"}, format="json")
        b = self.fresh(bill)
        show("B2 lot-2 objection", resp.status_code, "pay_by", b.pay_by(), "act_pay_by", b.act_pay_by(), "inst", self.rows(bill))

    def test_objection_standing_unpaid_report(self):
        order, line = self.order()
        r = self.receipt(order, line, d(2026, 6, 10), "10")
        bill = self.billfor(order, line, d(2026, 6, 25), "10", terms=self.net30)
        c = self.api("Warehouse Staff")
        c.post(f"/api/purchasing/goods-receipts/{r.pk}/objection/", {"objected_on": "2026-06-12", "objection": "bad"}, format="json")
        row = [x for x in msme_bills(JUNE, d(2027, 3, 31), as_of=d(2026, 12, 31)) if x["bill"] == bill.pk][0]
        b = self.fresh(bill)
        show("C objection standing, net30 bill 25 Jun, as of 31 Dec: pay_by", b.pay_by(), "inst", self.rows(bill),
             {k: str(v) for k, v in row.items() if k in ("due", "days_late", "at_risk", "unpaid")})
        # API surfaces
        api = self.api("AP Manager")
        for url in (f"/api/purchasing/bills/{bill.pk}/", "/api/purchasing/bills/"):
            resp = api.get(url)
            show("C GET", url, resp.status_code)

    def test_bill_ahead_of_goods(self):
        order, line = self.order()
        # a bill ahead of the goods is refused when the order is billed on receipt; print what happens
        try:
            bill = self.billfor(order, line, d(2026, 6, 5), "10")
            self.receipt(order, line, d(2026, 6, 20), "10")
            show("D billed 5 Jun, goods 20 Jun: pay_by", self.fresh(bill).pay_by(), self.rows(bill))
        except ValidationError as e:
            show("D refused:", str(e)[:100])

    def test_backdated_objection_erases_lateness(self):
        order, line = self.order()
        r = self.receipt(order, line, d(2026, 6, 10), "10")
        bill = self.billfor(order, line, d(2026, 6, 25), "10")
        self.pay(bill, "1000", d(2026, 9, 20))
        def row():
            return [x for x in msme_bills(JUNE, d(2027, 3, 31), as_of=d(2026, 9, 30)) if x["bill"] == bill.pk][0]
        x = row()
        show("E before: due", x["due"], "paid_on", x["paid_on"], "days_late", x["days_late"], "at_risk", x["at_risk"])
        c = self.api("Warehouse Staff")
        url = f"/api/purchasing/goods-receipts/{r.pk}/objection/"
        a = c.post(url, {"objected_on": "2026-06-12", "objection": "keyed on 30 Sep, dated 12 Jun"}, format="json")
        b = c.post(url, {"objection_removed_on": "2026-08-10"}, format="json")
        x = row()
        show("E after backdated objection (12 Jun) removed 10 Aug:", a.status_code, b.status_code, "due", x["due"], "days_late", x["days_late"], "at_risk", x["at_risk"])
        rr = GoodsReceipt.objects.get(pk=r.pk)
        show("E what is on record:", rr.objected_on, rr.objection_removed_on, rr.updated_at.date(), "updated_by", getattr(rr, "updated_by", None))

    def test_query_counts(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        order, line = self.order()
        self.receipt(order, line, d(2026, 6, 1), "6")
        self.receipt(order, line, d(2026, 6, 20), "4")
        bill = self.billfor(order, line, d(2026, 6, 25), "10")
        b = Bill.objects.get(pk=bill.pk)
        with CaptureQueriesContext(connection) as ctx:
            b.installments()
        show("queries for one installments() of a micro bill with 2 deliveries:", len(ctx))
        with CaptureQueriesContext(connection) as ctx:
            Bill.objects.get(pk=bill.pk).pay_by()
        show("queries for pay_by() (fresh object):", len(ctx))
