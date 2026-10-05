"""
The documents' lives, over the API, by the people whose job they are.

Coverage found that no test had ever driven a sales order, a delivery,
an invoice, a payment, a purchase order, a receipt or a bill through the
API: their models were tested to the penny, and every button a screen
would press had run only in a test that expected to be refused. Two
reports crashed the first time anything was asked of them that way.

The figures: ten widgets at 100.00 against 500 on the shelf at 4.00.
Six go on the first truck, four are left for the backorder. The order is
invoiced in full, 1,000.00; two are credited, 800.00; 300.00 comes in.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.sales.models import Delivery, Invoice
from apps.sales.tests_base import SalesTestCase


class LifecycleTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(f"{role.replace(' ', '_').lower()}-{User.objects.count()}")
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content[:400])
        return response.json() if response["Content-Type"].startswith("application/json") \
            else response


class ASaleTests(LifecycleTestCase):
    def take_the_order(self):
        rep = self.as_("Sales Rep")
        order = self.ok(rep.post("/api/sales/sales-orders/", {
            "customer": self.customer.pk, "order_date": "2026-03-01",
            "currency": self.usd.pk}, format="json"), 201)
        self.ok(rep.post("/api/sales/sales-order-lines/", {
            "order": order["id"], "item": self.item.pk, "uom": self.uom.pk,
            "quantity": "10", "unit_price": "100.00", "revenue_account": self.revenue.pk},
            format="json"), 201)
        confirmed = self.ok(rep.post(f"/api/sales/sales-orders/{order['id']}/confirm/"))
        self.assertEqual(confirmed["status"], "confirmed")
        return confirmed

    def ship(self, order, quantity):
        store = self.as_("Warehouse Staff")
        delivery = self.ok(store.post("/api/sales/deliveries/", {
            "sales_order": order["id"], "delivery_date": "2026-03-03"}, format="json"), 201)
        self.ok(store.post("/api/sales/delivery-lines/", {
            "delivery": delivery["id"], "order_line": order["lines"][0]["id"],
            "warehouse": self.warehouse.pk, "quantity_shipped": quantity}, format="json"), 201)
        return store, self.ok(store.post(f"/api/sales/deliveries/{delivery['id']}/post_delivery/"))

    def invoice(self, order):
        manager = self.as_("AR Manager")
        invoice = self.ok(manager.post(f"/api/sales/sales-orders/{order['id']}/create_invoice/",
                                       {"receivable_account": self.ar.pk,
                                        "invoice_date": "2026-03-04"}, format="json"))
        return manager, self.ok(manager.post(f"/api/sales/invoices/{invoice['id']}/post_invoice/"))

    def test_taken_shipped_and_left_on_backorder(self):
        order = self.take_the_order()
        store, shipped = self.ship(order, "6")
        self.assertTrue(shipped["posted"])
        rest = self.ok(store.post(f"/api/sales/deliveries/{shipped['id']}/backorder/"))
        self.assertEqual((rest["posted"], [line["quantity_shipped"] for line in rest["lines"]]),
                         (False, ["4.0000"]))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("494"))

    def test_invoiced_credited_and_paid(self):
        order = self.take_the_order()
        manager, invoice = self.invoice(order)
        self.assertEqual((invoice["posted"], invoice["total"], invoice["amount_due"]),
                         (True, "1000.00", "1000.00"))
        pdf = manager.get(f"/api/sales/invoices/{invoice['id']}/pdf/")
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))

        line = invoice["lines"][0]["id"]
        self.ok(manager.post(f"/api/sales/invoices/{invoice['id']}/credit_note/",
                             {"quantities": {str(line): "2"}}, format="json"))
        self.assertEqual(Invoice.objects.get(pk=invoice["id"]).amount_due(), Decimal("800.00"))

        payment = self.ok(manager.post("/api/accounting/payments/", {
            "party": self.customer.pk, "direction": "receipt", "payment_date": "2026-03-10",
            "amount": "300.00", "currency": self.usd.pk, "bank_account": self.bank.pk,
            "counterpart_account": self.ar.pk}, format="json"), 201)
        self.ok(manager.post(f"/api/accounting/payments/{payment['id']}/post_payment/"))
        self.ok(manager.post("/api/sales/invoice-payments/", {
            "invoice": invoice["id"], "payment": payment["id"], "amount": "300.00"},
            format="json"), 201)
        self.assertEqual(Invoice.objects.get(pk=invoice["id"]).amount_due(), Decimal("500.00"))

        # The cheque bounces.
        self.ok(manager.post(f"/api/accounting/payments/{payment['id']}/void/"))
        self.assertEqual(Invoice.objects.get(pk=invoice["id"]).amount_due(), Decimal("800.00"))

    def test_each_hand_does_only_its_own_part(self):
        order = self.take_the_order()
        rep = self.as_("Sales Rep")
        self.assertEqual(rep.post(f"/api/sales/sales-orders/{order['id']}/create_invoice/",
                                  {"receivable_account": self.ar.pk}, format="json"
                                  ).status_code, 200)
        draft = Invoice.objects.get(sales_order_id=order["id"])
        # The rep raises the invoice; posting it to the ledger is not theirs.
        self.assertEqual(rep.post(f"/api/sales/invoices/{draft.pk}/post_invoice/").status_code,
                         403)
        store = self.as_("Warehouse Staff")
        self.assertEqual(store.post(f"/api/sales/invoices/{draft.pk}/post_invoice/").status_code,
                         403)
        self.assertEqual(self.as_("GST Officer").post(
            f"/api/sales/invoices/{draft.pk}/post_invoice/").status_code, 403)


class TakingGoodsBackTests(LifecycleTestCase):
    def shipped_and_billed(self):
        sale = ASaleTests.take_the_order(self)
        store, shipped = ASaleTests.ship(self, sale, "6")
        ASaleTests.invoice(self, sale)
        return store, shipped

    def test_a_return_credits_what_was_billed(self):
        store, shipped = self.shipped_and_billed()
        back = self.ok(store.post(f"/api/sales/deliveries/{shipped['id']}/customer_return/",
                                  {}, format="json"))
        self.assertEqual(len(back["credit_notes"]), 1)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("500"))

    def test_a_replacement_credits_nothing_however_it_is_said(self):
        # Found writing this: "false" from a form was read as true, and a
        # replacement credited the invoice anyway.
        for said in (False, "false", "0", "no"):
            with self.subTest(said=said):
                store, shipped = self.shipped_and_billed()
                back = self.ok(store.post(
                    f"/api/sales/deliveries/{shipped['id']}/customer_return/",
                    {"credit_invoices": said}, format="json"))
                self.assertEqual(back["credit_notes"], [])

    def test_a_flag_that_is_neither_is_refused(self):
        store, shipped = self.shipped_and_billed()
        response = store.post(f"/api/sales/deliveries/{shipped['id']}/customer_return/",
                              {"credit_invoices": "maybe"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Delivery.objects.filter(reverses_id=shipped["id"]).exists())

    def test_a_credit_note_total_is_money_not_a_float(self):
        store, shipped = self.shipped_and_billed()
        back = self.ok(store.post(f"/api/sales/deliveries/{shipped['id']}/customer_return/",
                                  {}, format="json"))
        self.assertEqual(back["credit_notes"][0]["total"], "600.00")


class APurchaseTests(LifecycleTestCase):
    """
    Twenty widgets ordered at 5.00 from the fixture's vendor: received in
    full, billed 100.00, paid 60.00 of it, and the receipt partly returned.
    """

    def setUp(self):
        super().setUp()
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        self.vendor = Party.objects.create(code="V-1", name="Polymer Co",
                                           default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        from apps.accounting.models import Account, AccountType

        self.payable = Account.objects.create(code="2100", name="AP",
                                              account_type=AccountType.LIABILITY)
        from apps.core.models import Company

        company = Company.get()
        company.purchase_price_variance_account = Account.objects.create(
            code="5150", name="Price variance", account_type=AccountType.EXPENSE)
        company.save()

    def order_and_receive(self):
        clerk = self.as_("Purchasing Clerk")
        order = self.ok(clerk.post("/api/purchasing/purchase-orders/", {
            "vendor": self.vendor.pk, "order_date": "2026-03-01", "currency": self.usd.pk},
            format="json"), 201)
        self.ok(clerk.post("/api/purchasing/purchase-order-lines/", {
            "order": order["id"], "item": self.item.pk, "uom": self.uom.pk, "quantity": "20",
            "unit_price": "5.00"}, format="json"), 201)
        order = self.ok(clerk.post(f"/api/purchasing/purchase-orders/{order['id']}/confirm/"))
        store = self.as_("Warehouse Staff")
        receipt = self.ok(store.post("/api/purchasing/goods-receipts/", {
            "purchase_order": order["id"], "receipt_date": "2026-03-02"}, format="json"), 201)
        self.ok(store.post("/api/purchasing/goods-receipt-lines/", {
            "receipt": receipt["id"], "order_line": order["lines"][0]["id"],
            "warehouse": self.warehouse.pk, "quantity_received": "20"}, format="json"), 201)
        receipt = self.ok(store.post(
            f"/api/purchasing/goods-receipts/{receipt['id']}/post_receipt/"))
        return clerk, store, order, receipt

    def test_ordered_received_billed_and_paid(self):
        clerk, store, order, receipt = self.order_and_receive()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("520"))
        # Found writing this: the bill was raised from the raw account id
        # and crashed; a missing account was a KeyError, not a sentence.
        self.assertEqual(clerk.post(f"/api/purchasing/purchase-orders/{order['id']}/create_bill/",
                                    {}, format="json").status_code, 400)
        bill = self.ok(clerk.post(f"/api/purchasing/purchase-orders/{order['id']}/create_bill/",
                                  {"payable_account": self.payable.pk, "bill_date": "2026-03-05",
                                   "reference": "PC/889"}, format="json"))
        # The clerk raises it; the AP Manager posts it.
        self.assertEqual(clerk.post(f"/api/purchasing/bills/{bill['id']}/post_bill/").status_code,
                         403)
        ap = self.as_("AP Manager")
        bill = self.ok(ap.post(f"/api/purchasing/bills/{bill['id']}/post_bill/"))
        self.assertEqual((bill["posted"], bill["total"]), (True, "100.00"))

        payment = self.ok(ap.post("/api/accounting/payments/", {
            "party": self.vendor.pk, "direction": "disbursement", "payment_date": "2026-03-20",
            "amount": "60.00", "currency": self.usd.pk, "bank_account": self.bank.pk,
            "counterpart_account": self.payable.pk}, format="json"), 201)
        self.ok(ap.post(f"/api/accounting/payments/{payment['id']}/post_payment/"))
        # Found writing this: nothing over the API could say which bill a
        # payment settled; only the admin could.
        self.ok(ap.post("/api/purchasing/bill-payments/", {
            "bill": bill["id"], "payment": payment["id"], "amount": "60.00"}, format="json"), 201)
        bill = self.ok(ap.get(f"/api/purchasing/bills/{bill['id']}/"))
        self.assertEqual((bill["amount_paid"], bill["amount_due"], bill["settlement_status"]),
                         ("60.00", "40.00", "partial"))
        self.assertEqual(self.as_("Purchasing Clerk").post("/api/purchasing/bill-payments/", {
            "bill": bill["id"], "payment": payment["id"], "amount": "1.00"},
            format="json").status_code, 403)

    def test_received_and_sent_back(self):
        clerk, store, order, receipt = self.order_and_receive()
        back = self.ok(store.post(
            f"/api/purchasing/goods-receipts/{receipt['id']}/return_receipt/"))
        self.assertEqual((back["posted"], back["reverses"]), (True, receipt["id"]))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("500"))


    def test_the_receipt_names_the_batch_it_takes_in(self):
        # Found by review: the receipt line's lot was dropped by the API,
        # so polymer received over it could not be traced to its batch.
        from apps.inventory.models import Item, Lot

        Item.objects.filter(pk=self.item.pk).update(tracking="lot")
        clerk = self.as_("Purchasing Clerk")
        order = self.ok(clerk.post("/api/purchasing/purchase-orders/", {
            "vendor": self.vendor.pk, "order_date": "2026-03-01", "currency": self.usd.pk},
            format="json"), 201)
        self.ok(clerk.post("/api/purchasing/purchase-order-lines/", {
            "order": order["id"], "item": self.item.pk, "uom": self.uom.pk, "quantity": "25",
            "unit_price": "5.00"}, format="json"), 201)
        order = self.ok(clerk.post(f"/api/purchasing/purchase-orders/{order['id']}/confirm/"))
        store = self.as_("Warehouse Staff")
        lot = self.ok(store.post("/api/inventory/lots/", {
            "item": self.item.pk, "code": "RIL-H030SG-2611"}, format="json"), 201)
        receipt = self.ok(store.post("/api/purchasing/goods-receipts/", {
            "purchase_order": order["id"], "receipt_date": "2026-03-02"}, format="json"), 201)
        self.ok(store.post("/api/purchasing/goods-receipt-lines/", {
            "receipt": receipt["id"], "order_line": order["lines"][0]["id"],
            "warehouse": self.warehouse.pk, "quantity_received": "25", "lot": lot["id"]},
            format="json"), 201)
        self.ok(store.post(f"/api/purchasing/goods-receipts/{receipt['id']}/post_receipt/"))
        self.assertEqual(Lot.objects.get(pk=lot["id"]).on_hand_at(self.warehouse),
                         Decimal("25"))

from apps.hr.tests_leave import LeaveTestCase  # noqa: E402


class LeaveOverTheApiTests(LifecycleTestCase):
    """
    Asked for by the weaver, decided by their manager, each signed in as
    themselves: the login is the employee, so nobody names who decided.
    """

    employee = LeaveTestCase.employee

    def setUp(self):
        super().setUp()
        LeaveTestCase.setUp(self)
        self.weaver = self.employee("W-1")  # reports to self.boss

    def as_person(self, role, employee):
        client = self.as_(role)
        employee.user = User.objects.order_by("-pk").first()
        employee.save()
        return client

    def apply(self):
        own = self.as_person("Employee Self Service", self.weaver)
        return own, self.ok(own.post("/api/hr/leave-requests/", {
            "employee": self.weaver.pk, "policy": self.policy.pk, "leave_type": "vacation",
            "start_date": "2026-11-02", "end_date": "2026-11-04", "reason": "Wedding"},
            format="json"), 201)

    def test_applied_for_and_approved(self):
        own, leave = self.apply()
        # Nobody approves their own.
        self.assertEqual(own.post(f"/api/hr/leave-requests/{leave['id']}/approve/",
                                  {}, format="json").status_code, 403)
        manager = self.as_person("Line Manager", self.boss)
        # Not on someone else's behalf, even someone with the standing.
        other = self.employee("MGR-2", manager=None)
        refused = manager.post(f"/api/hr/leave-requests/{leave['id']}/approve/",
                               {"decided_by": other.pk}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        decided = self.ok(manager.post(f"/api/hr/leave-requests/{leave['id']}/approve/", {}, format="json"))
        self.assertEqual((decided["status"], decided["decided_by"]), ("approved", self.boss.pk))
        # It draws on the allowance it was asked against: three days of 25.
        from apps.hr.models import LeaveRequest, leave_balance

        self.assertEqual(LeaveRequest.objects.get(pk=leave["id"]).policy, self.policy)
        self.assertEqual(leave_balance(self.weaver, self.policy, 2026), Decimal("22"))

    def test_half_a_day_is_half_a_day(self):
        own = self.as_person("Employee Self Service", self.weaver)
        leave = self.ok(own.post("/api/hr/leave-requests/", {
            "employee": self.weaver.pk, "policy": self.policy.pk, "leave_type": "vacation",
            "start_date": "2026-11-02", "end_date": "2026-11-02", "half_day": True},
            format="json"), 201)
        # Counted, and frozen, when it is approved.
        decided = self.ok(self.as_person("Line Manager", self.boss).post(
            f"/api/hr/leave-requests/{leave['id']}/approve/", {}, format="json"))
        self.assertEqual((decided["half_day"], decided["days_taken"]), (True, "0.50"))

    def test_refused_with_a_reason(self):
        own, leave = self.apply()
        decided = self.ok(self.as_person("Line Manager", self.boss).post(
            f"/api/hr/leave-requests/{leave['id']}/reject/",
            {"reason": "Year-end dispatch"}, format="json"))
        self.assertEqual(decided["status"], "rejected")

    def test_a_manager_who_is_not_theirs_may_not_decide_and_cannot_see_it(self):
        own, leave = self.apply()
        stranger = self.as_person("Line Manager", self.employee("MGR-3", manager=None))
        self.assertEqual(stranger.post(f"/api/hr/leave-requests/{leave['id']}/approve/", {},
                                       format="json").status_code, 404)
        self.assertEqual(stranger.get("/api/hr/leave-requests/").json(), [])

    def test_leave_is_asked_for_oneself_and_read_by_whom_it_concerns(self):
        own, leave = self.apply()
        colleague = self.employee("W-2")
        refused = own.post("/api/hr/leave-requests/", {
            "employee": colleague.pk, "policy": self.policy.pk, "leave_type": "vacation",
            "start_date": "2026-11-09", "end_date": "2026-11-09"}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("employee", refused.json())
        peer = self.as_person("Employee Self Service", colleague)
        self.assertEqual(peer.get("/api/hr/leave-requests/").json(), [])
        self.assertEqual([row["id"] for row in own.get("/api/hr/leave-requests/").json()], [leave["id"]])
        hr = self.as_("HR Admin")
        self.assertEqual([row["id"] for row in hr.get("/api/hr/leave-requests/").json()], [leave["id"]])
        unlinked = self.as_("Employee Self Service")
        self.assertEqual(unlinked.get("/api/hr/leave-requests/").json(), [])

    def test_withdrawn_by_the_one_who_asked(self):
        own, leave = self.apply()
        self.assertEqual(self.ok(own.post(f"/api/hr/leave-requests/{leave['id']}/cancel/"))
                         ["status"], "cancelled")
