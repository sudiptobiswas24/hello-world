"""
The three gaps the audit checklist predicted and nobody had closed:
partial goods returns, returns that debit their own bills, and landed
cost billed separately from the goods it brought in.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import Account, AccountType, ChargeType, JournalLine
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment

from .models import (
    Bill,
    BillLine,
    GoodsReceipt,
    GoodsReceiptLine,
    LandedCostApplication,
    PurchaseOrder,
    PurchaseOrderLine,
    billed_not_held,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class ReturnTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.ppv = Account.objects.create(
            code="5900", name="PPV", account_type=AccountType.EXPENSE
        )
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.purchase_price_variance_account = self.ppv
        company.save()

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class PartialReturnTests(ReturnTestCase):
    def received(self, quantity="10", price="5"):
        order = self.make_order(quantity, price)
        receipt = self.receive(order, quantity)
        return order, receipt

    def test_part_of_a_receipt_can_go_back(self):
        """Returning ten to send back three loses the receipt date on the
        seven and books three stock movements where one belongs."""
        order, receipt = self.received("10", "5")
        line = receipt.lines.get()

        returned = receipt.create_return(quantities={line: Decimal("3")})

        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("7"))
        self.assertEqual(order.lines.first().quantity_received(), Decimal("7"))
        self.assertEqual(returned.lines.get().quantity_received, Decimal("3"))

    def test_the_rest_can_go_back_later(self):
        order, receipt = self.received("10", "5")
        line = receipt.lines.get()
        receipt.create_return(quantities={line: Decimal("3")})

        receipt.create_return(quantities={line: Decimal("7")})

        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("0"))
        self.assertEqual(line.quantity_returnable(), Decimal("0"))

    def test_the_same_goods_cannot_go_back_twice(self):
        order, receipt = self.received("10", "5")
        line = receipt.lines.get()
        receipt.create_return(quantities={line: Decimal("6")})

        with self.assertRaisesMessage(ValidationError, "left to return"):
            receipt.create_return(quantities={line: Decimal("6")})

    def test_returning_everything_is_still_the_default(self):
        order, receipt = self.received("10", "5")
        receipt.create_return()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("0"))

    def test_a_fully_returned_receipt_has_nothing_left(self):
        order, receipt = self.received("10", "5")
        receipt.create_return()

        with self.assertRaisesMessage(ValidationError, "nothing left on this receipt"):
            receipt.create_return()

    def test_another_receipt_s_line_is_refused(self):
        order, first = self.received("10", "5")
        second = self.receive(order, "0.5") if False else None
        other_order, other = self.received("4", "5")
        with self.assertRaisesMessage(ValidationError, "different receipt"):
            first.create_return(quantities={other.lines.get(): Decimal("1")})

    def test_a_return_cannot_be_returned(self):
        order, receipt = self.received("10", "5")
        returned = receipt.create_return()
        with self.assertRaisesMessage(ValidationError, "Cannot return a return"):
            returned.create_return()


class ReturnDebitsTheBillTests(ReturnTestCase):
    def billed(self, quantity="10", price="5"):
        order = self.make_order(quantity, price)
        receipt = self.receive(order, quantity)
        bill = order.create_bill(self.payable)
        bill.post()
        return order, receipt, bill

    def test_a_return_debits_the_bill_that_paid_for_it(self):
        """Sending goods back used to reverse the stock and leave the
        company still owing the vendor for them."""
        order, receipt, bill = self.billed("10", "5")
        self.assertEqual(bill.amount_due(), Decimal("50"))

        returned = receipt.create_return()

        self.assertEqual(len(returned.debit_notes_created), 1)
        self.assertEqual(bill.amount_due(), Decimal("0"))

    def test_a_partial_return_debits_only_its_share(self):
        order, receipt, bill = self.billed("10", "5")
        line = receipt.lines.get()

        receipt.create_return(quantities={line: Decimal("4")})

        self.assertEqual(bill.amount_due(), Decimal("30"))

    def test_a_replacement_raises_no_debit_note(self):
        """The vendor is replacing them, not refunding."""
        order, receipt, bill = self.billed("10", "5")

        returned = receipt.create_return(debit_bills=False)

        self.assertEqual(returned.debit_notes_created, [])
        self.assertEqual(bill.amount_due(), Decimal("50"))
        self.assertEqual(len(billed_not_held(vendor=self.vendor)), 1)

    def test_returning_unbilled_goods_debits_nothing(self):
        order = self.make_order("10", "5")
        receipt = self.receive(order, "10")

        returned = receipt.create_return()

        self.assertEqual(returned.debit_notes_created, [])

    def test_it_spreads_across_several_bills_oldest_first(self):
        order = self.make_order("10", "5")
        self.receive(order, "4")
        first = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        first.post()
        receipt = self.receive(order, "6")
        second = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 20))
        second.post()

        returned = receipt.create_return()

        # Six back, allocated oldest-first: four off the 4-unit first bill,
        # two off the 6-unit second. Receipts and bills are linked only
        # through the order line, so oldest-first is the convention, the
        # same one a customer return uses to pick invoices.
        self.assertEqual(len(returned.debit_notes_created), 2)
        self.assertEqual(first.amount_due(), Decimal("0"))
        self.assertEqual(second.amount_due(), Decimal("20"))

    def test_the_payable_ends_up_right(self):
        order, receipt, bill = self.billed("10", "5")
        receipt.create_return()
        self.assertEqual(self.balance(self.payable), Decimal("0"))


class ThirdPartyLandedCostTests(ReturnTestCase):
    """Freight, duty and the broker arrive as three bills, weeks apart,
    from three parties who never met."""

    def setUp(self):
        super().setUp()
        self.freight_expense = Account.objects.create(
            code="5300", name="Freight", account_type=AccountType.EXPENSE
        )
        self.freight = ChargeType.objects.create(
            code="FRT", name="Ocean freight",
            expense_account=self.freight_expense, capitalise_into_inventory=True,
        )
        self.carrier = Party.objects.create(
            code="V-CAR", name="Carrier", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.carrier, role=PartyRole.VENDOR)

    def goods_received(self, quantity="10", price="5"):
        order = self.make_order(quantity, price)
        receipt = self.receive(order, quantity)
        return order, receipt

    def carrier_bill(self, amount="80"):
        bill = Bill.objects.create(
            vendor=self.carrier, bill_date=datetime.date(2026, 2, 1),
            payable_account=self.payable, currency=self.usd,
        )
        line = BillLine.objects.create(
            bill=bill, charge=self.freight, description="Ocean freight",
            quantity=Decimal("1"), unit_price=Decimal(amount),
            expense_account=self.freight_expense,
        )
        bill.post()
        return bill, line

    def test_a_separate_freight_bill_expenses_until_allocated(self):
        order, receipt = self.goods_received()
        bill, charge = self.carrier_bill("80")

        self.assertEqual(self.balance(self.freight_expense), Decimal("80"))
        self.assertEqual(charge.landed_cost_unallocated(), Decimal("80"))

    def test_allocating_moves_it_into_stock(self):
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.carrier_bill("80")

        charge.allocate_landed_cost([receipt.lines.get()])

        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("13.0000"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("130.00"))

    def test_the_books_and_the_warehouse_agree_afterwards(self):
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.carrier_bill("80")
        before = self.balance(self.inventory)

        charge.allocate_landed_cost([receipt.lines.get()])

        self.assertEqual(self.balance(self.inventory) - before, Decimal("80"))
        self.assertEqual(self.balance(self.inventory), self.item.stock_value_at(self.warehouse))

    def test_it_splits_across_receipts_by_value(self):
        from apps.inventory.models import Item

        other = Item.objects.create(sku="W2", name="Gadget", uom=self.uom)
        first_order = self.make_order("10", "5")
        first = self.receive(first_order, "10")

        second_order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        second_line = PurchaseOrderLine.objects.create(
            order=second_order, item=other, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("15"),
        )
        second_order.confirm()
        second = GoodsReceipt.objects.create(
            purchase_order=second_order, receipt_date=datetime.date(2026, 1, 5)
        )
        GoodsReceiptLine.objects.create(
            receipt=second, order_line=second_line, warehouse=self.warehouse,
            quantity_received=Decimal("10"),
        )
        second.post()

        bill, charge = self.carrier_bill("80")
        charge.allocate_landed_cost([first.lines.get(), second.lines.get()])

        # 50 and 150 of goods, so 80 splits 20 / 60.
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("7.0000"))
        self.assertEqual(other.average_cost_at(self.warehouse), Decimal("21.0000"))

    def test_it_cannot_be_allocated_twice(self):
        order, receipt = self.goods_received()
        bill, charge = self.carrier_bill("80")
        charge.allocate_landed_cost([receipt.lines.get()])

        with self.assertRaisesMessage(ValidationError, "already been allocated in full"):
            charge.allocate_landed_cost([receipt.lines.get()])

    def test_releasing_takes_it_back_out(self):
        """A costing decision made weeks after the goods arrived is
        exactly the kind that gets revised."""
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.carrier_bill("80")
        applications = charge.allocate_landed_cost([receipt.lines.get()])

        applications[0].release()

        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("5.0000"))
        self.assertEqual(self.balance(self.freight_expense), Decimal("80"))
        self.assertEqual(charge.landed_cost_unallocated(), Decimal("80"))

    def test_it_cannot_be_released_twice(self):
        order, receipt = self.goods_received()
        bill, charge = self.carrier_bill("80")
        applications = charge.allocate_landed_cost([receipt.lines.get()])
        applications[0].release()

        with self.assertRaisesMessage(ValidationError, "already been released"):
            applications[0].release()

    def test_a_non_capitalising_charge_is_refused(self):
        plain = ChargeType.objects.create(
            code="CUR", name="Courier", expense_account=self.freight_expense
        )
        order, receipt = self.goods_received()
        bill = Bill.objects.create(
            vendor=self.carrier, bill_date=datetime.date(2026, 2, 1),
            payable_account=self.payable, currency=self.usd,
        )
        line = BillLine.objects.create(
            bill=bill, charge=plain, quantity=Decimal("1"), unit_price=Decimal("20"),
            expense_account=self.freight_expense,
        )
        bill.post()

        with self.assertRaisesMessage(ValidationError, "not a charge that capitalises"):
            line.allocate_landed_cost([receipt.lines.get()])

    def test_a_draft_bill_cannot_be_allocated(self):
        order, receipt = self.goods_received()
        bill = Bill.objects.create(
            vendor=self.carrier, bill_date=datetime.date(2026, 2, 1),
            payable_account=self.payable, currency=self.usd,
        )
        line = BillLine.objects.create(
            bill=bill, charge=self.freight, quantity=Decimal("1"),
            unit_price=Decimal("20"), expense_account=self.freight_expense,
        )
        with self.assertRaisesMessage(ValidationError, "Only a posted bill"):
            line.allocate_landed_cost([receipt.lines.get()])

    def test_it_cannot_land_on_goods_that_were_returned(self):
        order, receipt = self.goods_received()
        returned = receipt.create_return(debit_bills=False)
        bill, charge = self.carrier_bill("80")

        with self.assertRaisesMessage(ValidationError, "actually received"):
            charge.allocate_landed_cost([returned.lines.get()])

    def test_naming_no_goods_is_refused(self):
        bill, charge = self.carrier_bill("80")
        with self.assertRaisesMessage(ValidationError, "Name the goods"):
            charge.allocate_landed_cost([])
