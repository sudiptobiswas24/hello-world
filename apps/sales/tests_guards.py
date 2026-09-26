"""
The parallel-gap fixes: guards that existed on one path but not its mirror.

Each of these was a real defect found by probing the running system rather
than by the test suite, which had only ever exercised the happy path.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import (
    Account,
    AccountType,
    Payment,
    PaymentDirection,
)
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
)
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse

from .models import (
    CustomerProfile,
    Delivery,
    DeliveryLine,
    Invoice,
    InvoicePayment,
    OrderStatus,
    PriceList,
    PriceListItem,
    SalesOrder,
    SalesOrderLine,
    outstanding_balance,
)


class SalesGuardTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="WDG-1", name="Widget", uom=self.uom)
        self.warehouse = Warehouse.objects.create(code="WH1", name="Main")

        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET)
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
        Company.objects.create(
            name="Test Co", default_inventory_account=self.inventory,
            default_cogs_account=self.cogs, grni_account=self.grni,
        )

        self.customer = Party.objects.create(code="C-1", name="Acme", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
            uom=self.item.uom,
            quantity=Decimal("500"), unit_cost=Decimal("4"), occurred_at=timezone.now(),
        )

    def make_order(self, quantity="10", price="10", confirm=True):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1)
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            revenue_account=self.revenue,
        )
        if confirm:
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

    def bill(self, order):
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        invoice.post()
        return invoice


class CommittedOrderLineTests(SalesGuardTestCase):
    """Defect 1: a confirmed, shipped, invoiced line could be edited freely."""

    def test_quantity_cannot_drop_below_what_shipped(self):
        order = self.make_order("10")
        self.ship(order, "10")

        line = order.lines.get()
        line.quantity = Decimal("2")
        with self.assertRaises(ValidationError):
            line.save()

    def test_quantity_cannot_drop_below_what_was_invoiced(self):
        order = self.make_order("10")
        self.bill(order)

        line = order.lines.get()
        line.quantity = Decimal("2")
        with self.assertRaises(ValidationError):
            line.save()

    def test_quantity_can_still_be_increased(self):
        order = self.make_order("10")
        self.ship(order, "10")

        line = order.lines.get()
        line.quantity = Decimal("15")
        line.save()  # must not raise: adding to an order is legitimate
        self.assertEqual(order.lines.get().quantity, Decimal("15"))

    def test_price_cannot_change_once_invoiced(self):
        order = self.make_order("10", "10")
        self.bill(order)

        line = order.lines.get()
        line.unit_price = Decimal("999")
        with self.assertRaises(ValidationError):
            line.save()

    def test_price_can_change_before_invoicing(self):
        order = self.make_order("10", "10")
        line = order.lines.get()
        line.unit_price = Decimal("12")
        line.save()
        self.assertEqual(order.lines.get().unit_price, Decimal("12"))

    def test_a_committed_line_cannot_be_deleted(self):
        order = self.make_order("10")
        self.ship(order, "4")
        with self.assertRaises(ValidationError):
            order.lines.get().delete()

    def test_an_untouched_line_can_be_deleted(self):
        order = self.make_order("10", confirm=False)
        order.lines.get().delete()
        self.assertEqual(order.lines.count(), 0)


class CancelledOrderTests(SalesGuardTestCase):
    """Defect 2: a cancelled order could still be shipped."""

    def test_a_cancelled_order_cannot_be_shipped(self):
        order = self.make_order("10")
        order.cancel()

        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 3, 4)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=order.lines.get(),
            warehouse=self.warehouse, quantity_shipped=Decimal("5"),
        )
        with self.assertRaises(ValidationError):
            delivery.post()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("500"))

    def test_a_draft_order_cannot_be_shipped(self):
        order = self.make_order("10", confirm=False)
        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 3, 4)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=order.lines.get(),
            warehouse=self.warehouse, quantity_shipped=Decimal("5"),
        )
        with self.assertRaises(ValidationError):
            delivery.post()

    def test_cancelling_a_shipped_order_is_refused(self):
        order = self.make_order("10")
        self.ship(order, "4")
        with self.assertRaises(ValidationError):
            order.cancel()

    def test_cancelling_an_invoiced_order_is_refused(self):
        order = self.make_order("10")
        self.bill(order)
        with self.assertRaises(ValidationError):
            order.cancel()

    def test_cannot_cancel_twice(self):
        order = self.make_order("10")
        order.cancel()
        with self.assertRaises(ValidationError):
            order.cancel()

    def test_a_cancelled_order_cannot_be_confirmed_again(self):
        order = self.make_order("10")
        order.cancel()
        with self.assertRaises(ValidationError):
            order.confirm()


class PaymentDirectionTests(SalesGuardTestCase):
    """Defects 3 and 5: receipts settle invoices, disbursements refund credits."""

    def make_payment(self, amount, direction=PaymentDirection.RECEIPT):
        payment = Payment.objects.create(
            party=self.customer, direction=direction,
            payment_date=datetime.date(2026, 3, 10), amount=Decimal(amount),
            bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        return payment

    def test_a_receipt_cannot_be_applied_to_a_credit_note(self):
        order = self.make_order("10", "10")
        invoice = self.bill(order)
        credit_note = invoice.create_credit_note()
        receipt = self.make_payment("100")

        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(
                invoice=credit_note, payment=receipt, amount=Decimal("100")
            )

    def test_a_credit_note_is_refunded_with_a_disbursement(self):
        order = self.make_order("10", "10")
        invoice = self.bill(order)
        credit_note = invoice.create_credit_note()
        refund = self.make_payment("100", direction=PaymentDirection.DISBURSEMENT)

        allocation = InvoicePayment.objects.create(
            invoice=credit_note, payment=refund, amount=Decimal("100")
        )
        self.assertEqual(allocation.amount, Decimal("100"))
        self.assertEqual(credit_note.amount_paid(), Decimal("100"))

    def test_a_disbursement_still_cannot_settle_an_ordinary_invoice(self):
        order = self.make_order("10", "10")
        invoice = self.bill(order)
        disbursement = self.make_payment("100", direction=PaymentDirection.DISBURSEMENT)

        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(
                invoice=invoice, payment=disbursement, amount=Decimal("100")
            )


class LineQuantityConstraintTests(SalesGuardTestCase):
    """Defect 4: order lines accepted negative quantities."""

    def test_negative_order_line_quantity_is_rejected(self):
        order = self.make_order("10", confirm=False)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SalesOrderLine.objects.create(
                    order=order, item=self.item, uom=self.uom,
                    quantity=Decimal("-5"), unit_price=Decimal("10"),
                    revenue_account=self.revenue,
                )

    def test_zero_order_line_quantity_is_rejected(self):
        order = self.make_order("10", confirm=False)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SalesOrderLine.objects.create(
                    order=order, item=self.item, uom=self.uom,
                    quantity=Decimal("0"), unit_price=Decimal("10"),
                    revenue_account=self.revenue,
                )

    def test_negative_price_is_rejected(self):
        order = self.make_order("10", confirm=False)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SalesOrderLine.objects.create(
                    order=order, item=self.item, uom=self.uom,
                    quantity=Decimal("1"), unit_price=Decimal("-10"),
                    revenue_account=self.revenue,
                )


class CreditLimitTests(SalesGuardTestCase):
    def set_limit(self, amount):
        CustomerProfile.objects.update_or_create(
            party=self.customer, defaults={"credit_limit": Decimal(amount)}
        )

    def test_no_limit_means_no_check(self):
        self.make_order("1000", "100")  # 100,000 with no profile at all

    def test_an_order_within_the_limit_confirms(self):
        self.set_limit("5000")
        order = self.make_order("10", "100", confirm=False)
        order.confirm()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)

    def test_an_order_over_the_limit_is_refused(self):
        self.set_limit("500")
        order = self.make_order("10", "100", confirm=False)
        with self.assertRaises(ValidationError):
            order.confirm()
        self.assertEqual(order.status, OrderStatus.DRAFT)

    def test_existing_debt_counts_towards_the_limit(self):
        self.set_limit("1500")
        first = self.make_order("10", "100")
        self.bill(first)
        self.assertEqual(outstanding_balance(self.customer), Decimal("1000.00"))

        second = self.make_order("10", "100", confirm=False)
        with self.assertRaises(ValidationError):
            second.confirm()

    def test_paying_down_the_balance_frees_the_limit(self):
        self.set_limit("1500")
        first = self.make_order("10", "100")
        invoice = self.bill(first)

        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 3, 10), amount=Decimal("1000"),
            bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("1000"))

        self.assertEqual(outstanding_balance(self.customer), Decimal("0.00"))
        second = self.make_order("10", "100", confirm=False)
        second.confirm()  # must not raise

    def test_the_limit_is_cleared_by_approval_not_a_flag(self):
        """The old ignore_credit_limit= bypass was reachable by anyone who
        could confirm an order, and left no record of who decided."""
        self.set_limit("500")
        order = self.make_order("10", "100", confirm=False)

        order.approve(note="Long-standing customer")
        order.confirm()

        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        self.assertIsNotNone(order.approved_at)
        self.assertEqual(order.approval_note, "Long-standing customer")


class PricingTests(SalesGuardTestCase):
    def test_a_line_without_a_price_falls_back_to_the_item(self):
        self.item.sale_price = Decimal("12.50")
        self.item.save()

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1)
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("3"),
            revenue_account=self.revenue,
        )
        self.assertEqual(line.unit_price, Decimal("12.50"))
        self.assertEqual(line.net_amount(), Decimal("37.50"))

    def test_a_default_price_list_beats_the_item_price(self):
        self.item.sale_price = Decimal("12.50")
        self.item.save()
        price_list = PriceList.objects.create(
            code="STD", name="Standard", currency=self.usd, is_default=True
        )
        PriceListItem.objects.create(
            price_list=price_list, item=self.item, unit_price=Decimal("10.00")
        )

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            revenue_account=self.revenue,
        )
        self.assertEqual(line.unit_price, Decimal("10.00"))

    def test_a_customers_own_list_beats_the_default(self):
        default_list = PriceList.objects.create(
            code="STD", name="Standard", currency=self.usd, is_default=True
        )
        PriceListItem.objects.create(
            price_list=default_list, item=self.item, unit_price=Decimal("10.00")
        )
        special = PriceList.objects.create(code="VIP", name="VIP", currency=self.usd)
        PriceListItem.objects.create(
            price_list=special, item=self.item, unit_price=Decimal("7.00")
        )
        CustomerProfile.objects.create(party=self.customer, price_list=special)

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            revenue_account=self.revenue,
        )
        self.assertEqual(line.unit_price, Decimal("7.00"))

    def test_volume_breaks_pick_the_highest_qualifying_tier(self):
        price_list = PriceList.objects.create(
            code="STD", name="Standard", currency=self.usd, is_default=True
        )
        for min_quantity, price in (("1", "10"), ("10", "9"), ("100", "8")):
            PriceListItem.objects.create(
                price_list=price_list, item=self.item,
                min_quantity=Decimal(min_quantity), unit_price=Decimal(price),
            )

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        for quantity, expected in (("5", "10"), ("10", "9"), ("50", "9"), ("100", "8")):
            line = SalesOrderLine.objects.create(
                order=order, item=self.item, uom=self.uom,
                quantity=Decimal(quantity), revenue_account=self.revenue,
            )
            self.assertEqual(line.unit_price, Decimal(expected), f"at quantity {quantity}")

    def test_an_expired_price_list_is_ignored(self):
        self.item.sale_price = Decimal("12.50")
        self.item.save()
        expired = PriceList.objects.create(
            code="OLD", name="Last year", currency=self.usd, is_default=True,
            valid_to=datetime.date(2025, 12, 31),
        )
        PriceListItem.objects.create(
            price_list=expired, item=self.item, unit_price=Decimal("1.00")
        )

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            revenue_account=self.revenue,
        )
        self.assertEqual(line.unit_price, Decimal("12.50"))

    def test_an_explicit_price_always_wins(self):
        price_list = PriceList.objects.create(
            code="STD", name="Standard", currency=self.usd, is_default=True
        )
        PriceListItem.objects.create(
            price_list=price_list, item=self.item, unit_price=Decimal("10.00")
        )
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            unit_price=Decimal("3.33"), revenue_account=self.revenue,
        )
        self.assertEqual(line.unit_price, Decimal("3.33"))

    def test_an_unpriceable_item_fails_loudly(self):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1)
        )
        with self.assertRaises(ValidationError):
            SalesOrderLine.objects.create(
                order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
                revenue_account=self.revenue,
            )

    def test_price_list_dates_must_make_sense(self):
        price_list = PriceList(
            code="BAD", name="Bad", valid_from=datetime.date(2026, 6, 1),
            valid_to=datetime.date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            price_list.full_clean()
