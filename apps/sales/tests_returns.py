"""
Partial credit notes, and returns that actually refund the customer.

Before this, a customer return put stock back and reversed cost, but the
customer still owed the full invoice — the goods came back and the money
didn't follow.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, AccountType, Tax
from apps.core.models import (
    Company,
    Currency,
    ExchangeRate,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
)
from apps.inventory.models import Item, Warehouse

from .models import Delivery, DeliveryLine, Invoice, SalesOrder, SalesOrderLine, SettlementStatus


class ReturnsTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="WDG-1", name="Widget", uom=self.uom)
        self.warehouse = Warehouse.objects.create(code="WH1", name="Main")

        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        self.inventory = Account.objects.create(
            code="1200", name="Inventory", account_type=AccountType.ASSET
        )
        self.cogs = Account.objects.create(
            code="5000", name="Cost of Sales", account_type=AccountType.EXPENSE
        )
        self.grni = Account.objects.create(
            code="2150", name="GRNI", account_type=AccountType.LIABILITY
        )
        self.tax_payable = Account.objects.create(
            code="2100", name="Tax Payable", account_type=AccountType.LIABILITY
        )
        Company.objects.create(
            name="Test Co", default_inventory_account=self.inventory,
            default_cogs_account=self.cogs, grni_account=self.grni,
        )

        self.customer = Party.objects.create(code="C-1", name="Acme", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def stock_up(self, quantity="100", unit_cost="4"):
        from django.utils import timezone

        from apps.inventory.models import MovementType, StockMovement

        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
            quantity=Decimal(quantity), unit_cost=Decimal(unit_cost),
            occurred_at=timezone.now(),
        )

    def make_order(self, quantity="10", price="10", taxes=()):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1)
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            revenue_account=self.revenue,
        )
        if taxes:
            line.taxes.set(taxes)
        order.confirm()
        return order

    def ship(self, order, quantity):
        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 3, 4)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=order.lines.get(),
            warehouse=self.warehouse, quantity_shipped=Decimal(quantity),
        )
        delivery.post()
        return delivery

    def invoice(self, order):
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        invoice.post()
        return invoice


class PartialCreditNoteTests(ReturnsTestCase):
    def test_crediting_part_of_an_invoice(self):
        order = self.make_order("10", "10")
        invoice = self.invoice(order)
        line = invoice.lines.get()

        credit_note = invoice.create_credit_note(quantities={line: Decimal("3")})

        self.assertEqual(credit_note.total(), Decimal("30.00"))
        self.assertEqual(invoice.amount_credited(), Decimal("30.00"))
        self.assertEqual(invoice.amount_due(), Decimal("70.00"))
        self.assertEqual(invoice.settlement_status(), SettlementStatus.PARTIAL)

    def test_a_partial_credit_posts_the_mirror_of_the_invoice(self):
        order = self.make_order("10", "10")
        invoice = self.invoice(order)
        credit_note = invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("3")})

        entry = credit_note.journal_entry
        self.assertEqual(entry.lines.get(account=self.ar).credit, Decimal("30.00"))
        self.assertEqual(entry.lines.get(account=self.revenue).debit, Decimal("30.00"))
        self.assertEqual(entry.total_debit(), entry.total_credit())

    def test_a_partial_credit_is_not_marked_as_a_full_reversal(self):
        order = self.make_order("10", "10")
        invoice = self.invoice(order)
        credit_note = invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("3")})
        self.assertIsNone(credit_note.journal_entry.reverses)

    def test_a_full_credit_is_still_linked_as_a_reversal(self):
        order = self.make_order("10", "10")
        invoice = self.invoice(order)
        credit_note = invoice.create_credit_note()
        self.assertEqual(credit_note.journal_entry.reverses, invoice.journal_entry)

    def test_credits_accumulate_and_cannot_exceed_the_line(self):
        order = self.make_order("10", "10")
        invoice = self.invoice(order)
        line = invoice.lines.get()

        invoice.create_credit_note(quantities={line: Decimal("4")})
        self.assertEqual(line.quantity_credited(), Decimal("4"))
        self.assertEqual(line.quantity_creditable(), Decimal("6"))

        invoice.create_credit_note(quantities={line: Decimal("6")})
        self.assertEqual(invoice.amount_due(), Decimal("0.00"))

        with self.assertRaises(ValidationError):
            invoice.create_credit_note(quantities={line: Decimal("1")})

    def test_tax_is_credited_proportionally(self):
        vat = Tax.objects.create(
            code="VAT20", name="VAT 20%", rate=Decimal("20"),
            collected_account=self.tax_payable, paid_account=self.tax_payable,
        )
        order = self.make_order("10", "10", taxes=[vat])
        invoice = self.invoice(order)
        self.assertEqual(invoice.total(), Decimal("120.00"))

        credit_note = invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("5")})

        self.assertEqual(credit_note.total(), Decimal("60.00"))
        entry = credit_note.journal_entry
        self.assertEqual(entry.lines.get(account=self.tax_payable).debit, Decimal("10.00"))

    def test_crediting_nothing_is_refused(self):
        order = self.make_order("10", "10")
        invoice = self.invoice(order)
        with self.assertRaises(ValidationError):
            invoice.create_credit_note(quantities={})

    def test_a_partial_credit_frees_the_order_quantity_proportionally(self):
        order = self.make_order("10", "10")
        invoice = self.invoice(order)
        invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("4")})

        self.assertEqual(order.lines.get().quantity_invoiced(), Decimal("6"))
        remainder = order.create_invoice(self.ar)
        self.assertEqual(remainder.lines.get().quantity, Decimal("4"))


class ReturnRaisesCreditTests(ReturnsTestCase):
    def test_a_full_return_credits_the_whole_invoice(self):
        self.stock_up()
        order = self.make_order("10", "10")
        delivery = self.ship(order, "10")
        invoice = self.invoice(order)
        self.assertEqual(invoice.amount_due(), Decimal("100.00"))

        customer_return = delivery.create_return()

        self.assertEqual(len(customer_return.credit_notes_created), 1)
        self.assertEqual(invoice.amount_due(), Decimal("0.00"))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("100"))

    def test_a_partial_return_credits_only_what_came_back(self):
        self.stock_up()
        order = self.make_order("10", "10")
        delivery = self.ship(order, "4")
        self.ship(order, "6")
        invoice = self.invoice(order)

        delivery.create_return()  # only the first shipment of 4

        self.assertEqual(invoice.amount_credited(), Decimal("40.00"))
        self.assertEqual(invoice.amount_due(), Decimal("60.00"))

    def test_a_replacement_return_refunds_nothing(self):
        self.stock_up()
        order = self.make_order("10", "10")
        delivery = self.ship(order, "10")
        invoice = self.invoice(order)

        customer_return = delivery.create_return(credit_invoices=False)

        self.assertEqual(customer_return.credit_notes_created, [])
        self.assertEqual(invoice.amount_due(), Decimal("100.00"))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("100"))

    def test_returning_uninvoiced_goods_credits_nothing(self):
        self.stock_up()
        order = self.make_order("10", "10")
        delivery = self.ship(order, "10")

        customer_return = delivery.create_return()

        self.assertEqual(customer_return.credit_notes_created, [])
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("100"))

    def test_the_return_reverses_stock_cost_and_revenue_together(self):
        self.stock_up("100", unit_cost="4")
        order = self.make_order("10", "10")
        delivery = self.ship(order, "10")
        self.invoice(order)

        delivery.create_return()

        from django.db.models import Sum

        from apps.accounting.models import JournalLine

        def balance(account):
            rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
                debit=Sum("debit"), credit=Sum("credit")
            )
            return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

        self.assertEqual(balance(self.revenue), Decimal("0.00"))
        self.assertEqual(balance(self.cogs), Decimal("0.00"))
        self.assertEqual(balance(self.ar), Decimal("0.00"))

    def test_credit_is_spread_across_the_invoices_that_billed_the_goods(self):
        self.stock_up()
        order = self.make_order("10", "10")
        delivery = self.ship(order, "10")

        first = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        first.lines.update(quantity=Decimal("6"))
        first.post()
        second = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 8))
        second.post()

        customer_return = delivery.create_return()

        self.assertEqual(len(customer_return.credit_notes_created), 2)
        self.assertEqual(first.amount_due(), Decimal("0.00"))
        self.assertEqual(second.amount_due(), Decimal("0.00"))


class CreditNoteCurrencyTests(ReturnsTestCase):
    def test_credit_uses_the_rate_the_invoice_was_billed_at(self):
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=eur, rate=Decimal("1.10"), valid_from=datetime.date(2026, 1, 1)
        )
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=eur
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("10"), revenue_account=self.revenue,
        )
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        invoice.currency = eur
        invoice.save()
        invoice.post()
        self.assertEqual(invoice.journal_entry.lines.get(account=self.ar).debit, Decimal("110.00"))

        # The rate moves before the credit note is raised.
        ExchangeRate.objects.create(
            currency=eur, rate=Decimal("2.00"), valid_from=datetime.date(2026, 4, 1)
        )
        credit_note = invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("10")})

        self.assertEqual(credit_note.exchange_rate, Decimal("1.10"))
        self.assertEqual(
            credit_note.journal_entry.lines.get(account=self.ar).credit, Decimal("110.00")
        )
