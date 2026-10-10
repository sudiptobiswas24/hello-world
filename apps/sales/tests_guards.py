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
    InvoiceLine,
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
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
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

    def test_an_invoice_drafted_before_the_cancel_does_not_post_after_it(self):
        # Shipping asked whether the order still stood; invoicing did not, so the customer was
        # billed 100 for an order called off.
        order = self.make_order("10")
        draft = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 2))
        order.cancel()
        with self.assertRaisesMessage(ValidationError, "Only a confirmed order can be invoiced"):
            draft.post()
        self.assertEqual(outstanding_balance(self.customer), Decimal("0"))

    def test_an_invoice_typed_against_a_draft_order_does_not_post(self):
        order = self.make_order("10", confirm=False)
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 3, 2),
                                         sales_order=order, receivable_account=self.ar)
        InvoiceLine.objects.create(invoice=invoice, order_line=order.lines.get(), item=self.item,
                                   quantity=Decimal("10"), unit_price=Decimal("10"), revenue_account=self.revenue)
        with self.assertRaisesMessage(ValidationError, "is draft"):
            invoice.post()

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
        # Paid first. This test refunded a customer who had paid nothing,
        # and passed: the mirror of a defect purchasing never had.
        order = self.make_order("10", "10")
        invoice = self.bill(order)
        InvoicePayment.objects.create(invoice=invoice, payment=self.make_payment("100"),
                                      amount=Decimal("100"))
        credit_note = invoice.create_credit_note()
        refund = self.make_payment("100", direction=PaymentDirection.DISBURSEMENT)

        allocation = InvoicePayment.objects.create(
            invoice=credit_note, payment=refund, amount=Decimal("100")
        )
        self.assertEqual(allocation.amount, Decimal("100"))
        self.assertEqual(credit_note.amount_paid(), Decimal("100"))

    def test_nobody_is_refunded_what_they_never_paid(self):
        # Found by checking every test's receivables against the ledger:
        # crediting an unpaid invoice and then refunding the note paid the
        # customer 100.00 they had never paid, and the receivable account
        # said they owed it while every document said nothing was owed.
        order = self.make_order("10", "10")
        invoice = self.bill(order)
        credit_note = invoice.create_credit_note()
        self.assertEqual((invoice.amount_due(), credit_note.amount_due()),
                         (Decimal("0"), Decimal("0")))
        refund = self.make_payment("100", direction=PaymentDirection.DISBURSEMENT)
        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(invoice=credit_note, payment=refund,
                                          amount=Decimal("100"))

    def test_a_note_first_clears_what_is_unpaid_then_owes_the_rest(self):
        from .models import outstanding_balance

        order = self.make_order("10", "10")
        invoice = self.bill(order)
        InvoicePayment.objects.create(invoice=invoice, payment=self.make_payment("40"),
                                      amount=Decimal("40"))
        credit_note = invoice.create_credit_note()
        # 60 of the note clears what was unpaid; the 40 paid is owed back,
        # on the note, not as an invoice reading minus forty.
        self.assertEqual((invoice.amount_due(), credit_note.amount_due()),
                         (Decimal("0"), Decimal("40")))
        self.assertEqual(outstanding_balance(self.customer), Decimal("-40"))
        refund = self.make_payment("50", direction=PaymentDirection.DISBURSEMENT)
        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(invoice=credit_note, payment=refund,
                                          amount=Decimal("50"))
        InvoicePayment.objects.create(invoice=credit_note, payment=refund,
                                      amount=Decimal("40"))
        # The other 10 of the refund no note took is ours, with them: the
        # receivable says 100 - 40 - 100 + 50. The balance read 0 while
        # money paid on account counted for nothing.
        self.assertEqual((credit_note.amount_due(), outstanding_balance(self.customer)),
                         (Decimal("0"), Decimal("10")))

    def test_a_partial_credit_on_an_unpaid_invoice_leaves_the_rest_due(self):
        order = self.make_order("10", "10")
        invoice = self.bill(order)
        line = invoice.lines.first()
        credit_note = invoice.create_credit_note(quantities={line: Decimal("3")})
        self.assertEqual((invoice.amount_due(), credit_note.amount_due()),
                         (Decimal("70"), Decimal("0")))

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


# -- what has moved stays as it moved (shared rule A), and a line answers to its
# document (shared rule B). The 9 October sales audit's probes, kept as
# regression tests with their numbers.

from .tests_base import SalesTestCase as _AuditCase  # noqa: E402


class TradeRuleCase(_AuditCase):
    def setUp(self):
        super().setUp()
        from django.core.management import call_command

        from apps.core.models import ExchangeRate

        self.eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=self.eur, rate=Decimal("1.1"), valid_from=datetime.date(2026, 1, 1))
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        from django.contrib.auth.models import Group, User
        from rest_framework.test import APIClient

        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def other_customer(self, on_hold=False):
        other = Party.objects.create(code="C-2", name="Other")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        if on_hold:
            CustomerProfile.objects.create(party=other, credit_hold=True, credit_hold_reason="Unpaid since May")
        return other


class AShippedLineKeepsItsItemTests(TradeRuleCase):
    """O65: five widgets shipped; the line changed to gadgets, the return put five gadgets on the shelf."""

    def test_a_shipped_lines_item_does_not_change(self):
        gadget = Item.objects.create(sku="GDG-1", name="Gadget", uom=self.uom, sale_price=Decimal("10"))
        order = self.make_order("10", "100")
        delivery = self.ship(order, "5")
        line = order.lines.get()
        response = self.as_("AR Manager").patch(f"/api/sales/sales-order-lines/{line.pk}/",
                                                {"item": gadget.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("item", response.json())
        self.assertIn("shipped on", response.json()["item"][0])
        Delivery.objects.get(pk=delivery.pk).create_return(credit_invoices=False)
        self.assertEqual((gadget.on_hand_at(self.warehouse), self.item.on_hand_at(self.warehouse)),
                         (Decimal("0"), Decimal("500")))

    def test_an_invoiced_lines_price_and_discount_do_not_change(self):
        order = self.make_order("10", "100")
        self.bill(order)
        line = order.lines.get()
        line.discount_percent = Decimal("10")
        with self.assertRaisesMessage(ValidationError, "discount percent can no longer change"):
            line.save()


class AShippedOrderKeepsItsCustomerAndCurrencyTests(TradeRuleCase):
    """O68: a shipped order moved to another customer, on credit hold, in another currency."""

    def test_a_shipped_orders_customer_and_currency_do_not_change(self):
        other = self.other_customer(on_hold=True)
        order = self.make_order("10", "100")
        self.ship(order, "5")
        response = self.as_("AR Manager").patch(f"/api/sales/sales-orders/{order.pk}/",
                                                {"customer": other.pk, "currency": self.eur.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        order.refresh_from_db()
        self.assertEqual((order.customer, order.currency), (self.customer, self.usd))

    def test_a_draft_order_still_changes(self):
        other = self.other_customer()
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1),
                                          currency=self.usd)
        order.customer = other
        order.save()
        self.assertEqual(SalesOrder.objects.get(pk=order.pk).customer, other)


class AConfirmedOrderKeepsItsCustomerAndLinesTests(TradeRuleCase):
    """
    O138: rule A waited for the first movement, so a confirmed order's customer
    could change, or a line move between confirmed orders, with nothing confirm()
    asks asked.
    """

    def other_customer(self, limit=None):
        other = super().other_customer()
        CustomerProfile.objects.create(party=other, credit_limit=limit)
        return other

    def confirmed(self, customer, quantity, price):
        order = SalesOrder.objects.create(customer=customer, order_date=datetime.date(2026, 3, 1), currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
                                      unit_price=Decimal(price), revenue_account=self.revenue)
        order.confirm()
        return order

    def test_a_line_moved_onto_a_confirmed_order_is_refused(self):
        from .models import committed_balance

        other = self.other_customer(limit=Decimal("500"))
        theirs = self.confirmed(other, "1", "100")  # 100 of 500
        ours = self.make_order("10", "100")  # Acme, no limit
        response = self.as_("AR Manager").patch(f"/api/sales/sales-order-lines/{ours.lines.get().pk}/",
                                                {"order": theirs.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("been confirmed on", response.json()["order"][0])
        self.assertEqual(committed_balance(other), Decimal("100.00"))

    def test_a_draft_line_is_not_moved_onto_a_confirmed_order(self):
        other = self.other_customer(limit=Decimal("500"))
        theirs = self.confirmed(other, "1", "100")
        draft = SalesOrder.objects.create(customer=other, order_date=datetime.date(2026, 3, 1), currency=self.usd)
        line = SalesOrderLine.objects.create(order=draft, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                             unit_price=Decimal("100"), revenue_account=self.revenue)
        line.order = theirs
        with self.assertRaisesMessage(ValidationError, "made afresh on"):
            line.save()

    def test_a_line_moved_off_an_order_leaves_it_worth_its_deposits(self):
        ours = self.make_order("10", "100")
        ours.create_down_payment_invoice(self.ar, amount=Decimal("800"), invoice_date=datetime.date(2026, 3, 1)).post()
        second = self.make_order("1", "100")
        response = self.as_("AR Manager").patch(f"/api/sales/sales-order-lines/{ours.lines.get().pk}/",
                                                {"order": second.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        ours = SalesOrder.objects.get(pk=ours.pk)
        self.assertEqual((ours.total(), ours.deposit_total()), (Decimal("1000.00"), Decimal("800.00")))

    def test_a_confirmed_order_moved_to_another_customer_is_refused(self):
        from .models import committed_balance

        other = self.other_customer(limit=Decimal("500"))
        ours = self.make_order("10", "100")  # confirmed for Acme, nothing shipped
        response = self.as_("AR Manager").patch(f"/api/sales/sales-orders/{ours.pk}/", {"customer": other.pk},
                                                format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("has been confirmed; its customer can no longer change", response.json()["customer"][0])
        self.assertEqual(committed_balance(other), Decimal("0"))


class APostedDeliveryKeepsItsLinesTests(TradeRuleCase):
    """O66: a posted delivery's line deleted over the API by Warehouse Staff."""

    def test_a_posted_delivery_keeps_its_lines(self):
        order = self.make_order("10", "100")
        delivery = self.ship(order, "5")
        line = delivery.lines.get()
        response = self.as_("Warehouse Staff").delete(f"/api/sales/delivery-lines/{line.pk}/")
        self.assertEqual((response.status_code, order.lines.get().quantity_shipped(),
                          self.item.on_hand_at(self.warehouse)), (400, Decimal("5"), Decimal("495")))

    def test_a_line_is_not_moved_off_a_posted_delivery(self):
        order = self.make_order("10", "100")
        posted = self.ship(order, "5")
        draft = order.create_delivery(warehouse=self.warehouse)
        line = posted.lines.get()
        line.delivery = draft
        with self.assertRaisesMessage(ValidationError, "posted delivery"):
            line.save()


class AnInvoiceLineBillsOnlyItsOwnOrderTests(TradeRuleCase):
    """O71: an invoice line naming another customer's order line."""

    def test_an_invoice_line_cannot_bill_another_customers_order_line(self):
        other = self.other_customer()
        order = self.make_order("10", "100")  # Acme's
        invoice = Invoice.objects.create(customer=other, invoice_date=datetime.date(2026, 3, 1),
                                         receivable_account=self.ar, currency=self.usd)
        with self.assertRaisesMessage(ValidationError, "Acme"):
            InvoiceLine.objects.create(invoice=invoice, order_line=order.lines.get(), item=self.item,
                                       quantity=Decimal("10"), unit_price=Decimal("1"), revenue_account=self.revenue)
        self.assertEqual((order.lines.get().quantity_invoiced(), order.invoice_status()), (Decimal("0"), "none"))

    def test_an_invoice_line_bills_only_its_own_invoices_order_through_the_api(self):
        other = self.other_customer()
        order = self.make_order("10", "100")
        client = self.as_("AR Manager")
        made = client.post("/api/sales/invoices/", {"customer": other.pk, "invoice_date": "2026-03-01",
                                                    "currency": self.usd.pk, "receivable_account": self.ar.pk},
                           format="json")
        self.assertEqual(made.status_code, 201, made.data)
        line = client.post("/api/sales/invoice-lines/", {
            "invoice": made.data["id"], "order_line": order.lines.get().pk, "item": self.item.pk,
            "quantity": "10", "unit_price": "1.00", "revenue_account": self.revenue.pk}, format="json")
        self.assertEqual(line.status_code, 400, line.content)
        self.assertIn("order_line", line.json())

    def test_the_customer_changed_after_the_line_is_asked_again_as_it_posts(self):
        other = self.other_customer()
        order = self.make_order("10", "100")
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        invoice.customer = other
        invoice.save()
        with self.assertRaisesMessage(ValidationError, "Acme"):
            Invoice.objects.get(pk=invoice.pk).post()
        self.assertEqual(order.lines.get().quantity_invoiced(), Decimal("0"))


class OneShipmentIsInvoicedOnceTests(TradeRuleCase):
    """O67: on a bill-on-delivery order two drafts for one shipment of 5 both posted: 10 billed."""

    def test_one_shipment_is_not_invoiced_twice(self):
        from .models import InvoicePolicy

        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.ship(order, "5")
        first = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        second = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))  # the first still a draft
        first.post()
        with self.assertRaisesMessage(ValidationError, "bills on delivery"):
            Invoice.objects.get(pk=second.pk).post()
        line = order.lines.get()
        self.assertEqual((line.quantity_invoiced(), line.quantity_shipped(), self.balance(self.ar)),
                         (Decimal("5"), Decimal("5"), Decimal("500.00")))


class AConfirmedOrderAsksThePolicyAgainTests(TradeRuleCase):
    """O72: a confirmed line raised to 60% against a 15% policy, and a 60% line added, both stood."""

    def setUp(self):
        super().setUp()
        from .models import ApprovalPolicy

        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=Decimal("15"))

    def test_a_confirmed_line_is_not_discounted_past_the_policy(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1),
                                          currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                      unit_price=Decimal("100"), discount_percent=Decimal("10"),
                                      revenue_account=self.revenue)
        order.confirm()  # 10% is within 15%: no approval needed
        line = order.lines.get()
        line.discount_percent = Decimal("60")
        with self.assertRaisesMessage(ValidationError, "needs approval"):
            line.save()
        self.assertEqual(order.lines.get().net_amount(), Decimal("900.00"))

    def test_a_line_added_to_a_confirmed_order_asks_the_policy(self):
        order = self.make_order("1", "100")
        with self.assertRaisesMessage(ValidationError, "needs approval"):
            SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                          unit_price=Decimal("100"), discount_percent=Decimal("60"),
                                          revenue_account=self.revenue)
        self.assertEqual(order.lines.count(), 1)

    def test_a_line_within_the_policy_is_still_added(self):
        order = self.make_order("1", "100")
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                      unit_price=Decimal("100"), discount_percent=Decimal("15"),
                                      revenue_account=self.revenue)
        self.assertEqual(order.lines.count(), 2)


class ReopeningALineAsksTheCreditLimitTests(TradeRuleCase):
    """O73: reopened with nothing asked, exposure 1,800 against a limit of 1,000."""

    def test_reopening_a_line_asks_the_credit_limit(self):
        from .models import committed_balance

        CustomerProfile.objects.create(party=self.customer, credit_limit=Decimal("1000"))
        first = self.make_order("10", "100")  # 1000, at the limit
        self.ship(first, "2")
        line = first.lines.get()
        line.close_short("Customer wants no more")
        self.make_order("8", "100")  # 800: 200 + 800 = 1000, within
        self.assertEqual(committed_balance(self.customer), Decimal("1000.00"))
        with self.assertRaisesMessage(ValidationError, "would be at 1800.00 against a credit limit of 1000"):
            SalesOrderLine.objects.get(pk=line.pk).reopen()
        self.assertTrue(SalesOrderLine.objects.get(pk=line.pk).is_closed_short())
        self.assertEqual(committed_balance(self.customer), Decimal("1000.00"))


class ACreditLineCreditsOnlyItsNotesInvoiceTests(TradeRuleCase):
    """O140: rule B asked the order line a line names, not the invoice line it credits."""

    def test_an_invoice_line_credits_only_its_own_invoices_lines(self):
        other = self.other_customer()
        acme = self.bill(self.make_order("10", "100"))  # Acme's invoice, 10 creditable
        client = self.as_("AR Manager")
        made = client.post("/api/sales/invoices/", {"customer": other.pk, "invoice_date": "2026-03-01",
                                                    "currency": self.usd.pk, "receivable_account": self.ar.pk},
                           format="json")
        line = client.post("/api/sales/invoice-lines/", {
            "invoice": made.data["id"], "credits_line": acme.lines.get().pk, "item": self.item.pk,
            "quantity": "10", "unit_price": "1.00", "revenue_account": self.revenue.pk}, format="json")
        self.assertEqual(line.status_code, 400, line.content)
        self.assertIn("credits_line", line.json())
        self.assertEqual(acme.lines.get().quantity_creditable(), Decimal("10"))

    def test_a_note_gives_back_no_more_than_the_line_holds(self):
        acme = self.bill(self.make_order("10", "100"))
        line = acme.lines.get()

        def typed_note():
            note = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 3, 5),
                                          receivable_account=self.ar, currency=self.usd, credits=acme)
            InvoiceLine.objects.create(invoice=note, credits_line=line, order_line=line.order_line, item=self.item,
                                       quantity=Decimal("10"), unit_price=Decimal("100"), revenue_account=self.revenue)
            return note

        typed_note().post()
        with self.assertRaisesMessage(ValidationError, "Only 0 of"):
            typed_note().post()
        self.assertEqual((line.quantity_creditable(), self.balance(self.ar)), (Decimal("0"), Decimal("0.00")))
