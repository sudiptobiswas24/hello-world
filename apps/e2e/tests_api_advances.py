"""
Money taken and paid up front, over the API.

Down payments and prepayments existed in the models with no way to make
one from a screen, so a plant that takes an advance on every sack order
could not record it. And once made in code, a draft could be given a
second line over the API that crashed its credit note.

An order of ten at 100.00 takes 30% up front, 300.00; a purchase of
twenty at 5.00 pays 30%, 30.00.
"""

from decimal import Decimal

from django.contrib.auth.models import Permission, User
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment

from . import tests_api_lifecycle as lifecycle
from .tests_api_lifecycle import LifecycleTestCase


def holding(*codenames):
    """A client for somebody with exactly these permissions."""
    user = User.objects.create_user(f"only-{User.objects.count()}")
    for codename in codenames:
        app, name = codename.split(".")
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app, codename=name))
    client = APIClient()
    client.force_authenticate(user)
    return client


class ADepositOverTheApiTests(LifecycleTestCase):
    # Borrowed, not inherited: importing the class would run its tests twice.
    take_the_order = lifecycle.ASaleTests.take_the_order

    def deposit(self, manager, order, **figures):
        return self.ok(manager.post(f"/api/sales/sales-orders/{order['id']}/down_payment/", {
            "receivable_account": self.ar.pk, "invoice_date": "2026-03-01", **figures},
            format="json"), 201)

    def test_taken_posted_and_returned(self):
        order = self.take_the_order()
        manager = self.as_("AR Manager")
        deposit = self.deposit(manager, order, percent="30")
        self.assertEqual((deposit["is_down_payment"], deposit["total"]), (True, "300.00"))
        self.ok(manager.post(f"/api/sales/invoices/{deposit['id']}/post_invoice/"))
        note = self.ok(manager.post(f"/api/sales/invoices/{deposit['id']}/credit_note/"))
        self.assertEqual((note["total"], self.balance(self.deposits)),
                         ("300.00", Decimal("0")))

    def test_a_second_line_is_refused_when_posted_not_crashed_later(self):
        order = self.take_the_order()
        manager = self.as_("AR Manager")
        deposit = self.deposit(manager, order, amount="300")
        self.ok(manager.post("/api/sales/invoice-lines/", {
            "invoice": deposit["id"], "description": "Bank charge", "quantity": "1",
            "unit_price": "5.00", "revenue_account": self.revenue.pk}, format="json"), 201)
        refused = manager.post(f"/api/sales/invoices/{deposit['id']}/post_invoice/")
        self.assertEqual(refused.status_code, 400, refused.content[:300])
        self.assertIn("single line", refused.content.decode())

    def test_a_figure_that_is_not_a_number_is_refused(self):
        order = self.take_the_order()
        refused = self.as_("AR Manager").post(
            f"/api/sales/sales-orders/{order['id']}/down_payment/",
            {"receivable_account": self.ar.pk, "amount": "a lot"}, format="json")
        self.assertEqual(refused.status_code, 400)

    def test_a_sales_rep_drafts_one_and_cannot_post_it(self):
        """As with any invoice: the rep prepares it, the AR Manager posts it."""
        order = self.take_the_order()
        rep = self.as_("Sales Rep")
        deposit = self.deposit(rep, order, percent="30")
        refused = rep.post(f"/api/sales/invoices/{deposit['id']}/post_invoice/")
        self.assertEqual(refused.status_code, 403)


    def test_raising_one_takes_the_right_to_raise_invoices(self):
        """Not the right to change orders, which is all the route says."""
        order = self.take_the_order()
        refused = holding("sales.view_salesorder", "sales.add_salesorder").post(
            f"/api/sales/sales-orders/{order['id']}/down_payment/",
            {"receivable_account": self.ar.pk, "percent": "30"}, format="json")
        self.assertEqual(refused.status_code, 403)


class APrepaymentOverTheApiTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.vendor = Party.objects.create(code="V-1", name="Polymer Co",
                                           default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.payable = Account.objects.create(code="2100", name="AP",
                                              account_type=AccountType.LIABILITY)
        self.prepaid = Account.objects.create(code="1400", name="Vendor prepayments",
                                              account_type=AccountType.ASSET)
        company = Company.get()
        company.vendor_prepayment_account = self.prepaid
        company.save()

    def ordered(self):
        clerk = self.as_("Purchasing Clerk")
        order = self.ok(clerk.post("/api/purchasing/purchase-orders/", {
            "vendor": self.vendor.pk, "order_date": "2026-03-01", "currency": self.usd.pk},
            format="json"), 201)
        self.ok(clerk.post("/api/purchasing/purchase-order-lines/", {
            "order": order["id"], "item": self.item.pk, "uom": self.uom.pk, "quantity": "20",
            "unit_price": "5.00"}, format="json"), 201)
        return self.ok(clerk.post(f"/api/purchasing/purchase-orders/{order['id']}/confirm/"))

    def prepay(self, order):
        manager = self.as_("AP Manager")
        bill = self.ok(manager.post(
            f"/api/purchasing/purchase-orders/{order['id']}/prepayment/",
            {"payable_account": self.payable.pk, "percent": "30", "bill_date": "2026-03-01"},
            format="json"), 201)
        return manager, bill

    def test_paid_up_front_posted_and_given_back(self):
        manager, bill = self.prepay(self.ordered())
        self.assertEqual((bill["is_prepayment"], bill["total"]), (True, "30.00"))
        self.ok(manager.post(f"/api/purchasing/bills/{bill['id']}/post_bill/"))
        note = self.ok(manager.post(f"/api/purchasing/bills/{bill['id']}/debit_note/"))
        self.assertEqual((note["total"], self.balance(self.prepaid)), ("30.00", Decimal("0")))

    def test_a_second_line_is_refused_when_posted(self):
        manager, bill = self.prepay(self.ordered())
        expense = Account.objects.create(code="5900", name="Bank charges",
                                         account_type=AccountType.EXPENSE)
        self.ok(manager.post("/api/purchasing/bill-lines/", {
            "bill": bill["id"], "description": "Bank charge", "quantity": "1",
            "unit_price": "5.00", "expense_account": expense.pk}, format="json"), 201)
        refused = manager.post(f"/api/purchasing/bills/{bill['id']}/post_bill/")
        self.assertEqual(refused.status_code, 400, refused.content[:300])
        self.assertIn("single line", refused.content.decode())

    def test_raising_one_takes_the_right_to_raise_bills(self):
        order = self.ordered()
        refused = holding("purchasing.view_purchaseorder", "purchasing.add_purchaseorder").post(
            f"/api/purchasing/purchase-orders/{order['id']}/prepayment/",
            {"payable_account": self.payable.pk, "percent": "30"}, format="json")
        self.assertEqual(refused.status_code, 403)
