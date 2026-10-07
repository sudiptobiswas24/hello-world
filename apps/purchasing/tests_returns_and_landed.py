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
from apps.core.models import Company, Currency, ExchangeRate, Party, PartyRole, PartyRoleAssignment

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
from apps.assets.models import AssetCategory

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


class ReturnedAtTheShelfsCostTests(ReturnTestCase):
    """
    500 on the shelf at 4.00, then 20 bought at 5.00: 520 averaging
    4.0385. Sending the 20 back takes 80.77 off the shelf, which is what
    the replay removes; the vendor owes back the 100.00 they were paid.
    The 19.23 between is a gain on the price, not stock that is not there.

    Found tracing stock against the ledger across every test: the return
    credited inventory at the 100.00 it cost, the shelf gave up 80.77, and
    the two parted by 19.23 for good. cost_of_removing() is the rule every
    outbound path follows, and the return to vendor did not ask it.
    """

    def test_the_shelf_and_the_ledger_still_agree(self):
        from apps.inventory.reports import reconcile_to_ledger

        self.receive(self.make_order("500", "4"), "500")
        order = self.make_order("20", "5")
        receipt = self.receive(order, "20")
        returned = receipt.create_return(debit_bills=False)
        self.assertEqual(returned.price_difference_entry.lines.get(account=self.ppv).credit, Decimal("19.23"))
        report = reconcile_to_ledger()
        self.assertEqual((report["total_stock_value"], report["difference"]),
                         (Decimal("2019.23"), Decimal("0.00")))
        self.assertEqual(self.balance(self.ppv), Decimal("-19.23"))
        self.assertEqual(self.balance(self.grni), Decimal("-2000.00"))

    def test_a_return_at_the_average_posts_no_variance(self):
        self.receive(self.make_order("500", "4"), "500")
        receipt = self.receive(self.make_order("20", "4"), "20")
        receipt.create_return(debit_bills=False)
        self.assertEqual(self.balance(self.ppv), Decimal("0"))


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

    def test_and_the_report_says_so_over_the_api(self):
        # Found crawling every endpoint over each test's data: the report
        # crashed on its first row, having only been asked with none.
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        order, receipt, bill = self.billed("10", "5")
        receipt.create_return(debit_bills=False)
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("buyer"))
        response = client.get("/api/purchasing/purchasing-reports/billed-not-held/")
        self.assertEqual(response.status_code, 200, response.content[:300])
        (row,) = response.json()
        self.assertEqual((row["order"], row["vendor"], Decimal(row["quantity"]),
                          Decimal(row["value"])),
                         (order.number, str(self.vendor), Decimal("10"), Decimal("50.00")))

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


class OneChargeLandsOnceTests(ThirdPartyLandedCostTests):
    """A freight bill could land on the goods and be capitalised onto a machine as well: 80.00
    of freight became 80.00 of stock and 80.00 of asset, and the freight account went to -80.00."""

    def setUp(self):
        super().setUp()
        accounts = {code: Account.objects.create(code=code, name=name, account_type=kind) for code, name, kind in (
            ("1500", "Plant", AccountType.ASSET), ("1590", "Accumulated depreciation", AccountType.ASSET),
            ("6100", "Depreciation", AccountType.EXPENSE), ("7100", "Disposals", AccountType.EXPENSE))}
        self.category = AssetCategory.objects.create(
            code="PLANT", name="Plant", asset_account=accounts["1500"], accumulated_account=accounts["1590"],
            expense_account=accounts["6100"], disposal_account=accounts["7100"], default_life_months=12)

    def test_landed_on_the_goods_it_is_not_also_a_machine(self):
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.carrier_bill("80")
        charge.allocate_landed_cost([receipt.lines.get()])
        with self.assertRaisesMessage(ValidationError, "has been landed on stock"):
            charge.capitalise_as_asset(self.category)
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.balance(self.category.asset_account), Decimal("0"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("130.00"))

    def test_on_a_machine_it_does_not_also_land_on_the_goods(self):
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.carrier_bill("80")
        charge.capitalise_as_asset(self.category)
        with self.assertRaisesMessage(ValidationError, "is capitalised as a fixed asset"):
            charge.allocate_landed_cost([receipt.lines.get()])
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.balance(self.category.asset_account), Decimal("80"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("50.00"))

    def test_undone_from_the_machine_it_may_land_on_the_goods(self):
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.carrier_bill("80")
        charge.capitalise_as_asset(self.category)[0].uncapitalise()
        charge.allocate_landed_cost([receipt.lines.get()])
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.balance(self.category.asset_account), Decimal("0"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("130.00"))

    def test_released_from_the_goods_it_may_go_on_a_machine(self):
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.carrier_bill("80")
        charge.allocate_landed_cost([receipt.lines.get()])[0].release()
        charge.capitalise_as_asset(self.category)
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.balance(self.category.asset_account), Decimal("80"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("50.00"))


class WhereTheFreightIsTests(OneChargeLandsOnceTests):
    """
    A freight charge's cost sits in one place at a time: the freight account
    (billed alone), the goods (landed by its own bill, or later), or a
    machine. A refund, a landing or a capitalisation takes it from where it
    is. Each used to assume the freight account, at the bill's own figures:
    a refunded charge still landed, one billed with its goods landed twice,
    a refund of landed freight emptied an account that never held it, and
    a euro bill put one figure on the ledger and another on the shelf.
    """

    def refund(self, bill, quantities=None):
        return bill.create_debit_note(memo="Refunded", quantities=quantities)

    def freight_in_two(self):
        bill = Bill.objects.create(vendor=self.carrier, bill_date=datetime.date(2026, 2, 1),
                                   payable_account=self.payable, currency=self.usd)
        line = BillLine.objects.create(bill=bill, charge=self.freight, description="Ocean freight",
                                       quantity=Decimal("2"), unit_price=Decimal("40"),
                                       expense_account=self.freight_expense)
        bill.post()
        return bill, line

    def with_its_goods(self):
        order, receipt = self.goods_received("10", "5")
        bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        freight = BillLine.objects.create(bill=bill, charge=self.freight, description="Freight on this lot",
                                          quantity=Decimal("1"), unit_price=Decimal("80"),
                                          expense_account=self.freight_expense)
        bill.post()
        return bill, freight

    def euro(self):
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("1.10"), valid_from=datetime.date(2026, 1, 1))
        return eur

    def test_a_refunded_charge_does_not_land(self):
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.carrier_bill("80")
        self.refund(bill)
        with self.assertRaisesMessage(ValidationError, "allocated in full, or given back"):
            charge.allocate_landed_cost([receipt.lines.get()])
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("50.00"))

    def test_what_is_left_of_a_part_refunded_charge_lands(self):
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.freight_in_two()
        self.refund(bill, {charge: Decimal("1")})
        charge.allocate_landed_cost([receipt.lines.get()])
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("90.00"))

    def test_a_landed_charge_is_released_before_it_is_refunded(self):
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.carrier_bill("80")
        (application,) = charge.allocate_landed_cost([receipt.lines.get()])
        with self.assertRaisesMessage(ValidationError, "has been landed on stock. Release that first"):
            self.refund(bill)
        application.release()
        self.refund(bill)
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("50.00"))
        self.assertEqual(self.balance(self.inventory), Decimal("50"))

    def drafted_refund(self, bill, charge):
        """A vendor's credit note typed in, and left in draft while the charge moves."""
        note = Bill.objects.create(vendor=self.carrier, bill_date=datetime.date(2026, 2, 5), debits=bill,
                                   payable_account=self.payable, currency=self.usd)
        BillLine.objects.create(bill=note, debits_line=charge, charge=self.freight, description="Freight refund",
                                quantity=Decimal("1"), unit_price=Decimal("80"), expense_account=self.freight_expense)
        return note

    def test_a_refund_drafted_before_the_landing_is_refused_as_it_posts(self):
        order, receipt = self.goods_received("10", "5")
        bill, charge = self.carrier_bill("80")
        note = self.drafted_refund(bill, charge)
        charge.allocate_landed_cost([receipt.lines.get()])
        with self.assertRaisesMessage(ValidationError, "has been landed on stock"):
            note.post()
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))

    def test_a_refund_drafted_before_the_capitalising_is_refused_as_it_posts(self):
        bill, charge = self.carrier_bill("80")
        note = self.drafted_refund(bill, charge)
        charge.capitalise_as_asset(self.category)
        with self.assertRaisesMessage(ValidationError, "was capitalised as fixed assets"):
            note.post()
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))

    def test_a_euro_charge_lands_at_the_rate_it_was_booked(self):
        eur = self.euro()
        order, receipt = self.goods_received("10", "5")
        bill = Bill.objects.create(vendor=self.carrier, bill_date=datetime.date(2026, 2, 1),
                                   payable_account=self.payable, currency=eur)
        charge = BillLine.objects.create(bill=bill, charge=self.freight, description="Ocean freight",
                                         quantity=Decimal("1"), unit_price=Decimal("80"),
                                         expense_account=self.freight_expense)
        bill.post()
        self.assertEqual(self.balance(self.freight_expense), Decimal("88.00"))
        charge.allocate_landed_cost([receipt.lines.get()])
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("138.00"))
        self.assertEqual(self.balance(self.inventory), Decimal("138.00"))

    def test_a_charge_its_bill_landed_does_not_land_or_capitalise_again(self):
        bill, freight = self.with_its_goods()
        other_order, other_receipt = self.goods_received("10", "5")
        with self.assertRaisesMessage(ValidationError, "when it posted; it is in their cost already"):
            freight.allocate_landed_cost([other_receipt.lines.get()])
        with self.assertRaisesMessage(ValidationError, "when it posted; its cost is in their stock value"):
            freight.capitalise_as_asset(self.category)
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.balance(self.inventory), Decimal("180"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("180.00"))

    def test_a_refund_of_freight_its_bill_landed_comes_off_the_goods(self):
        bill, freight = self.with_its_goods()
        self.refund(bill, {freight: Decimal("1")})
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.balance(self.inventory), Decimal("50"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("50.00"))
        self.assertEqual(self.balance(self.payable), Decimal("-50"))

    def test_a_full_refund_takes_the_landed_freight_off_the_shelf_too(self):
        bill, freight = self.with_its_goods()
        self.refund(bill)
        self.assertEqual(self.balance(self.inventory), Decimal("50"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("50.00"))

    def test_a_euro_bills_landed_freight_is_one_figure_on_shelf_and_ledger(self):
        self.vendor.default_currency = self.euro()
        self.vendor.save()
        self.with_its_goods()
        self.assertEqual(self.balance(self.inventory), Decimal("143.00"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("143.00"))

    def test_what_is_left_of_a_part_refunded_line_is_capitalised(self):
        bill, charge = self.freight_in_two()
        self.refund(bill, {charge: Decimal("1")})
        (asset,) = charge.capitalise_as_asset(self.category)
        self.assertEqual(asset.cost, Decimal("40.00"))
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))

    def test_a_line_refunded_in_full_is_not_capitalised(self):
        bill, charge = self.carrier_bill("80")
        self.refund(bill)
        with self.assertRaisesMessage(ValidationError, "given back in full"):
            charge.capitalise_as_asset(self.category)
        self.assertEqual(self.balance(self.category.asset_account), Decimal("0"))
