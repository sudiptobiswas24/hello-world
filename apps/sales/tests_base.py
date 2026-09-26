"""Shared fixture for the Sales test suite."""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import (
    Account,
    AccountType,
    JournalLine,
    Payment,
    PaymentDirection,
)
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    PaymentTerms,
    UnitOfMeasure,
)
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse

from .models import (
    Delivery,
    DeliveryLine,
    InvoicePayment,
    InvoicePolicy,
    SalesOrder,
    SalesOrderLine,
)


class SalesTestCase(TestCase):
    """
    One fixture for the Sales suite: a base currency, a stocked item, a
    full chart of accounts, a customer on 2/10 net 30 and a sales rep,
    plus the helpers that turn those into confirmed orders, shipments
    and posted invoices.
    """

    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(
            sku="WDG-1", name="Widget", uom=self.uom, sale_price=Decimal("10")
        )
        self.warehouse = Warehouse.objects.create(code="WH1", name="Main")

        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET)
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        self.discount_account = Account.objects.create(
            code="5100", name="Settlement Discounts", account_type=AccountType.EXPENSE
        )
        self.inventory = Account.objects.create(
            code="1200", name="Inventory", account_type=AccountType.ASSET
        )
        self.cogs = Account.objects.create(
            code="5000", name="COGS", account_type=AccountType.EXPENSE
        )
        self.grni = Account.objects.create(
            code="2150", name="GRNI", account_type=AccountType.LIABILITY
        )
        self.bad_debt = Account.objects.create(
            code="5200", name="Bad Debt Expense", account_type=AccountType.EXPENSE
        )
        self.deposits = Account.objects.create(
            code="2200", name="Customer Deposits", account_type=AccountType.LIABILITY
        )
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory, default_cogs_account=self.cogs,
            grni_account=self.grni, settlement_discount_account=self.discount_account,
            bad_debt_account=self.bad_debt, customer_deposit_account=self.deposits,
        )

        self.terms = PaymentTerms.objects.create(
            code="2-10-N30", name="2/10 Net 30", net_days=30,
            discount_percent=Decimal("2"), discount_days=10,
        )
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd,
            payment_terms=self.terms, email="ap@acme.example",
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        self.rep = Party.objects.create(code="E-1", name="Dana")
        PartyRoleAssignment.objects.create(party=self.rep, role=PartyRole.EMPLOYEE)

        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
            uom=self.item.uom,
            quantity=Decimal("500"), unit_cost=Decimal("4"), occurred_at=timezone.now(),
        )

    def make_order(self, quantity="10", price="100", policy=InvoicePolicy.ORDERED, rep=None):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1),
            currency=self.usd, invoice_policy=policy, sales_rep=rep,
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
            unit_price=Decimal(price), revenue_account=self.revenue,
        )
        order.confirm()
        return order

    def ship(self, order, quantity):
        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 3, 3)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=order.lines.filter(charge__isnull=True).first(),
            warehouse=self.warehouse, quantity_shipped=Decimal(quantity),
        )
        delivery.post()
        return delivery

    def bill(self, order, on=datetime.date(2026, 3, 1)):
        invoice = order.create_invoice(self.ar, invoice_date=on)
        invoice.post()
        return invoice

    def receipt(self, amount, on=datetime.date(2026, 3, 10)):
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=on, amount=Decimal(amount), currency=self.usd,
            bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        return payment

    def allocate(self, payment, invoice, amount):
        return InvoicePayment.objects.create(
            invoice=invoice, payment=payment, amount=Decimal(amount)
        )

    def balance(self, account):
        from django.db.models import Sum

        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))
