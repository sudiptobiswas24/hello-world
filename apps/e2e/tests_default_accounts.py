"""
The accounts a document books to when it names none.

Before these existed, an order line left without a revenue account was
invoiced into a database error (IntegrityError, a 500), and every screen
raising an invoice, a bill or a payment had to make its clerk choose the
control account again.

Refusals first: with no default set, each path says which account is
missing, beside the field, and leaves nothing half made.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, Payment
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.purchasing.models import (
    Bill,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
)
from apps.sales.models import Invoice, SalesOrder, SalesOrderLine
from apps.sales.tests_base import SalesTestCase, carries_every_customer

DAY = datetime.date(2026, 3, 2)


class DefaultsTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.payable = Account.objects.create(code="2000", name="AP", account_type=AccountType.LIABILITY)
        self.vendor = Party.objects.create(code="V-1", name="Granule Co", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        if role == "Sales Rep":
            carries_every_customer(user)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def set_defaults(self, **accounts):
        company = Company.get()
        for name, account in accounts.items():
            setattr(company, f"default_{name}_account", account)
        company.save()

    def order_without_revenue_account(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                      quantity=Decimal("2"), unit_price=Decimal("10"))
        order.confirm()
        return order


class RevenueAccountTests(DefaultsTestCase):
    def test_no_default_is_a_sentence_beside_the_field_and_no_invoice(self):
        order = self.order_without_revenue_account()
        response = self.as_("AR Manager").post(
            f"/api/sales/sales-orders/{order.pk}/create_invoice/", {"receivable_account": self.ar.pk},
            format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("revenue account", str(response.json()))
        self.assertEqual(Invoice.objects.count(), 0)

    def test_the_default_takes_the_line_and_the_sale_posts_to_it(self):
        self.set_defaults(revenue=self.revenue)
        order = self.order_without_revenue_account()
        response = self.as_("AR Manager").post(
            f"/api/sales/sales-orders/{order.pk}/create_invoice/", {"receivable_account": self.ar.pk},
            format="json")
        self.assertEqual(response.status_code, 200, response.content)
        invoice = Invoice.objects.get(pk=response.json()["id"])
        self.assertEqual(invoice.lines.get().revenue_account, self.revenue)
        invoice.post()
        self.assertEqual(self.balance(self.revenue), Decimal("-20.00"))

    def test_a_line_naming_its_own_keeps_it(self):
        other = Account.objects.create(code="4100", name="Other sales", account_type=AccountType.INCOME)
        self.set_defaults(revenue=self.revenue)
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar)
        response = self.as_("AR Manager").post("/api/sales/invoice-lines/", {
            "invoice": invoice.pk, "item": self.item.pk, "quantity": "1", "unit_price": "5",
            "revenue_account": other.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["revenue_account"], other.pk)

    def test_a_typed_line_without_one_takes_the_default(self):
        self.set_defaults(revenue=self.revenue)
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar)
        response = self.as_("AR Manager").post("/api/sales/invoice-lines/", {
            "invoice": invoice.pk, "item": self.item.pk, "quantity": "1", "unit_price": "5"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["revenue_account"], self.revenue.pk)


class ReceivableAccountTests(DefaultsTestCase):
    def setUp(self):
        super().setUp()
        self.set_defaults(revenue=self.revenue)

    def test_no_default_names_the_field(self):
        order = self.order_without_revenue_account()
        response = self.as_("AR Manager").post(f"/api/sales/sales-orders/{order.pk}/create_invoice/", {},
                                               format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("receivable_account", response.json())
        self.assertEqual(Invoice.objects.count(), 0)

        response = self.as_("Sales Rep").post("/api/sales/invoices/", {
            "customer": self.customer.pk, "invoice_date": "2026-03-02"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("receivable_account", response.json())

    def test_invoicing_an_order_and_typing_an_invoice_take_the_default(self):
        self.set_defaults(receivable=self.ar)
        order = self.order_without_revenue_account()
        response = self.as_("AR Manager").post(f"/api/sales/sales-orders/{order.pk}/create_invoice/", {},
                                               format="json")
        self.assertEqual((response.status_code, response.json()["receivable_account"]), (200, self.ar.pk))

        response = self.as_("Sales Rep").post("/api/sales/invoices/", {
            "customer": self.customer.pk, "invoice_date": "2026-03-02"}, format="json")
        self.assertEqual((response.status_code, response.json()["receivable_account"]), (201, self.ar.pk))

    def test_a_down_payment_takes_it_too(self):
        self.set_defaults(receivable=self.ar)
        order = self.order_without_revenue_account()
        response = self.as_("AR Manager").post(f"/api/sales/sales-orders/{order.pk}/down_payment/",
                                               {"percent": "50"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["receivable_account"], self.ar.pk)


class PayableAccountTests(DefaultsTestCase):
    def received_order(self):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=DAY, currency=self.usd)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                         quantity=Decimal("10"), unit_price=Decimal("4"))
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=DAY)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(),
                                        warehouse=self.warehouse, quantity_received=Decimal("10"))
        receipt.post()
        return order

    def test_no_default_names_the_field_and_makes_no_bill(self):
        order = self.received_order()
        response = self.as_("AP Manager").post(f"/api/purchasing/purchase-orders/{order.pk}/create_bill/", {},
                                               format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("payable_account", response.json())
        self.assertEqual(Bill.objects.count(), 0)

    def test_billing_an_order_and_typing_a_bill_take_the_default(self):
        self.set_defaults(payable=self.payable)
        order = self.received_order()
        response = self.as_("AP Manager").post(f"/api/purchasing/purchase-orders/{order.pk}/create_bill/", {},
                                               format="json")
        self.assertEqual((response.status_code, response.json()["payable_account"]), (200, self.payable.pk))

        response = self.as_("Purchasing Clerk").post("/api/purchasing/bills/", {
            "vendor": self.vendor.pk, "bill_date": "2026-03-02", "reference": "GC-7"}, format="json")
        self.assertEqual((response.status_code, response.json()["payable_account"]), (201, self.payable.pk))


class PaymentAccountTests(DefaultsTestCase):
    def pay(self, client, party, direction, **extra):
        return client.post("/api/accounting/payments/", {
            "party": party.pk, "direction": direction, "payment_date": "2026-03-10",
            "amount": "100", **extra}, format="json")

    def test_no_default_bank_is_named(self):
        response = self.pay(self.as_("AR Manager"), self.customer, "receipt", counterpart_account=self.ar.pk)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("bank_account", response.json())
        self.assertEqual(Payment.objects.count(), 0)

    def test_money_in_settles_receivables_and_money_out_payables(self):
        self.set_defaults(bank=self.bank, receivable=self.ar, payable=self.payable)
        received = self.pay(self.as_("AR Manager"), self.customer, "receipt")
        self.assertEqual(received.status_code, 201, received.content)
        self.assertEqual((received.json()["bank_account"], received.json()["counterpart_account"]),
                         (self.bank.pk, self.ar.pk))
        paid = self.pay(self.as_("AP Manager"), self.vendor, "disbursement")
        self.assertEqual(paid.status_code, 201, paid.content)
        self.assertEqual(paid.json()["counterpart_account"], self.payable.pk)

    def test_money_out_with_no_payable_default_is_refused_not_booked_to_receivables(self):
        self.set_defaults(bank=self.bank, receivable=self.ar)
        response = self.pay(self.as_("AP Manager"), self.vendor, "disbursement")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("counterpart_account", response.json())
        self.assertIn("payable", str(response.json()))
