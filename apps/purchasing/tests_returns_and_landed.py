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
    BillPolicy,
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


class ReturnsOnlyWhatIsOnTheShelfTests(ReturnTestCase):
    """
    Ten received, eight shipped, two left. A delivery has always asked
    the shelf before taking from it; the return to vendor did not, and
    sending the ten back left the shelf at minus eight in a warehouse
    that refuses to go below nothing.
    """

    def shipped_eight_of_ten(self):
        from django.utils import timezone

        from apps.inventory.models import MovementType, StockMovement

        receipt = self.receive(self.make_order("10", "5"), "10")
        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.ISSUE,
            uom=self.uom, quantity=Decimal("-8"), unit_cost=Decimal("5"),
            occurred_at=timezone.now(), notes="shipped",
        )
        return receipt

    def test_goods_no_longer_on_the_shelf_cannot_go_back(self):
        receipt = self.shipped_eight_of_ten()
        with self.assertRaisesMessage(ValidationError, "cannot send back 10"):
            receipt.create_return(debit_bills=False)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("2"))
        self.assertFalse(GoodsReceipt.objects.filter(reverses=receipt).exists())
        self.assertEqual(self.balance(self.inventory), Decimal("50"))

    def test_what_is_still_there_can_go_back(self):
        receipt = self.shipped_eight_of_ten()
        receipt.create_return(quantities={receipt.lines.get(): Decimal("2")}, debit_bills=False)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("0"))

    def test_a_warehouse_that_allows_backorders_takes_it_back_anyway(self):
        self.warehouse.allow_negative_stock = True
        self.warehouse.save()
        receipt = self.shipped_eight_of_ten()
        receipt.create_return(debit_bills=False)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("-8"))


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

    def test_it_debits_the_bill_that_paid_for_the_receipt_returned(self):
        order = self.make_order("10", "5")
        self.receive(order, "4")
        first = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        first.post()
        receipt = self.receive(order, "6")
        second = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 20))
        second.post()

        returned = receipt.create_return()

        # The six of the second receipt go back, and the second bill (6 @ 5,
        # 30.00) is the one that paid for them: it is debited in full, and
        # the first (4 @ 5, 20.00) is still owed. Oldest-first took four off
        # the first bill for goods it never paid for (O94; this test encoded
        # that, 0 and 20, until then).
        self.assertEqual(len(returned.debit_notes_created), 1)
        self.assertEqual(first.amount_due(), Decimal("20"))
        self.assertEqual(second.amount_due(), Decimal("0"))

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

    def test_it_is_not_released_before_it_was_allocated_or_on_a_day_to_come(self):
        from django.utils import timezone

        order, receipt = self.goods_received()
        bill, charge = self.carrier_bill("80")
        application = charge.allocate_landed_cost([receipt.lines.get()], on_date=datetime.date(2026, 2, 10))[0]

        with self.assertRaisesMessage(ValidationError, "is not released on 2026-02-09: it was allocated on 2026-02-10."):
            application.release(on_date=datetime.date(2026, 2, 9))
        with self.assertRaisesMessage(ValidationError, "that day has not come"):
            application.release(on_date=timezone.localdate() + datetime.timedelta(days=1))
        application.refresh_from_db()
        self.assertFalse(application.is_released())
        self.assertEqual(application.release(on_date=datetime.date(2026, 2, 10)).date, datetime.date(2026, 2, 10))

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


class LandedWhereTheGoodsAreTests(ThirdPartyLandedCostTests):
    """
    Ten at 5 waiting in the receiving bay, then 80 of freight. It went on
    the stock room the line was destined for: under FIFO the bay's layer
    is not there, so the shelf kept 50 and the books said 130; under the
    average an empty stock room held 80. And with the goods already gone
    the books took 80 of stock nobody had. The freight goes on the shelf
    the receipt put the goods on, and what no shelf carries is cost of
    sales.
    """

    def setUp(self):
        super().setUp()
        self.cogs = Account.objects.create(code="5100", name="Cost of sales",
                                           account_type=AccountType.EXPENSE)
        company = Company.get()
        company.default_cogs_account = self.cogs
        company.save()

    def via_the_bay(self):
        from apps.inventory.models import Warehouse

        self.bay = Warehouse.objects.create(code="BAY", name="Receiving bay")
        self.warehouse.receipt_route = "input"
        self.warehouse.input_warehouse = self.bay
        self.warehouse.save()

    def first_in_first_out(self):
        self.item.costing_method = "fifo"
        self.item.save()

    def written_off(self):
        from apps.inventory.models import AdjustmentReason, StockAdjustment, StockAdjustmentLine

        reason = AdjustmentReason.objects.create(code="SCRAP", name="Scrapped", account=self.expense)
        adjustment = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 1, 20),
                                                    warehouse=self.warehouse, reason=reason)
        StockAdjustmentLine.objects.create(adjustment=adjustment, item=self.item, uom=self.uom,
                                           quantity=Decimal("-10"))
        adjustment.post()

    def landed(self):
        bill, charge = self.carrier_bill("80")
        (application,) = charge.allocate_landed_cost([self.receipt.lines.get()])
        return application

    def test_freight_on_goods_waiting_in_the_bay_lands_on_them(self):
        from apps.inventory.reports import reconcile_to_ledger

        self.first_in_first_out()
        self.via_the_bay()
        order, self.receipt = self.goods_received("10", "5")
        self.landed()
        self.assertEqual(self.item.stock_value_at(self.bay), Decimal("130.00"))
        self.assertEqual(reconcile_to_ledger()["difference"], Decimal("0.00"))

    def test_under_the_average_the_bay_carries_it_not_an_empty_stock_room(self):
        self.via_the_bay()
        order, self.receipt = self.goods_received("10", "5")
        self.landed()
        self.assertEqual((self.item.stock_value_at(self.bay), self.item.stock_value_at(self.warehouse)),
                         (Decimal("130.00"), Decimal("0.00")))

    def test_freight_on_goods_already_written_off_goes_where_the_write_off_posted(self):
        # The integrator's ruling (R1): the write-off took 50 to its account;
        # the 80 that landed on those goods follows: 130 there, cost of sales 0.
        self.first_in_first_out()
        order, self.receipt = self.goods_received("10", "5")
        self.written_off()
        self.landed()
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs), self.balance(self.expense)),
                         (Decimal("0"), Decimal("0"), Decimal("130")))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("0.00"))

    def test_released_it_comes_back_off_the_bay(self):
        self.first_in_first_out()
        self.via_the_bay()
        order, self.receipt = self.goods_received("10", "5")
        application = self.landed()
        # On the bay first, or the release proves nothing: landed on the
        # stock room, FIFO dropped it there and the bay read 50 throughout.
        self.assertEqual(self.item.stock_value_at(self.bay), Decimal("130.00"))
        application.release()
        self.assertEqual(self.item.stock_value_at(self.bay), Decimal("50.00"))
        self.assertEqual((self.balance(self.inventory), self.balance(self.freight_expense)),
                         (Decimal("50"), Decimal("80")))

    def test_released_after_the_goods_were_written_off_it_comes_out_of_the_write_offs_account(self):
        # Written off at 130 to the reason's account, the freight left with the
        # goods; released, that account gives the 80 back (R1): 50 there.
        self.first_in_first_out()
        order, self.receipt = self.goods_received("10", "5")
        application = self.landed()
        self.written_off()  # at 130: the freight left with the goods
        application.release()
        self.assertEqual((self.balance(self.inventory), self.item.stock_value_at(self.warehouse)),
                         (Decimal("0"), Decimal("0.00")))
        self.assertEqual((self.balance(self.cogs), self.balance(self.freight_expense), self.balance(self.expense)),
                         (Decimal("0"), Decimal("80"), Decimal("50")))

    def test_released_when_it_never_reached_a_shelf_it_comes_back_from_where_it_went(self):
        self.first_in_first_out()
        order, self.receipt = self.goods_received("10", "5")
        self.written_off()
        application = self.landed()
        # On the write-off's account while it stands (50 + 80, R1), or the
        # release proves nothing: debited to inventory, the release credited
        # inventory back and every balance below came out the same.
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs), self.balance(self.expense)),
                         (Decimal("0"), Decimal("0"), Decimal("130")))
        application.release()
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs),
                          self.balance(self.freight_expense), self.balance(self.expense)),
                         (Decimal("0"), Decimal("0"), Decimal("80"), Decimal("50")))


    # Ten received into the bay and put away before the freight bill came:
    # the 80 went to cost of sales because the bay was empty, while the
    # stock room held all ten at 50. The freight follows the goods to where
    # they are now, each shelf taking the share its goods there bear, and
    # only what no shelf holds is cost of sales.

    def put_away(self, quantity=None):
        self.receipt.lines.get().advance(quantity=None if quantity is None else Decimal(quantity))

    def test_freight_after_the_goods_were_put_away_lands_in_the_stock_room(self):
        self.via_the_bay()
        order, self.receipt = self.goods_received("10", "5")
        self.put_away()
        self.landed()
        self.assertEqual((self.item.stock_value_at(self.warehouse), self.item.stock_value_at(self.bay)),
                         (Decimal("130.00"), Decimal("0.00")))
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs)),
                         (Decimal("130"), Decimal("0")))

    def test_under_fifo_it_lands_on_the_layer_the_put_away_made(self):
        from apps.inventory.reports import reconcile_to_ledger

        self.first_in_first_out()
        self.via_the_bay()
        order, self.receipt = self.goods_received("10", "5")
        self.put_away()
        self.landed()
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("130.00"))
        self.assertEqual(reconcile_to_ledger()["difference"], Decimal("0.00"))

    def test_six_put_away_and_four_in_the_bay_share_it_by_quantity(self):
        self.via_the_bay()
        order, self.receipt = self.goods_received("10", "5")
        self.put_away("6")
        self.landed()
        self.assertEqual((self.item.stock_value_at(self.bay), self.item.stock_value_at(self.warehouse)),
                         (Decimal("52.00"), Decimal("78.00")))
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs)),
                         (Decimal("130.00"), Decimal("0")))

    def test_released_each_shelf_gives_back_what_it_took(self):
        self.via_the_bay()
        order, self.receipt = self.goods_received("10", "5")
        self.put_away("6")
        application = self.landed()
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("78.00"))
        application.release()
        self.assertEqual((self.item.stock_value_at(self.bay), self.item.stock_value_at(self.warehouse)),
                         (Decimal("20.00"), Decimal("30.00")))
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs),
                          self.balance(self.freight_expense)),
                         (Decimal("50.00"), Decimal("0.00"), Decimal("80.00")))

    def test_on_one_left_of_ten_only_its_share_lands(self):
        order, self.receipt = self.goods_received("10", "5")
        self.written_off_but_one()
        self.landed()
        # Nine of ten written off (45 to the reason's account): the one left
        # bears 8, and the 72 on the nine follows them to that account (R1).
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("13.00"))
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs),
                          self.balance(self.freight_expense), self.balance(self.expense)),
                         (Decimal("13.00"), Decimal("0"), Decimal("0.00"), Decimal("117.00")))

    def test_under_fifo_goods_cleared_from_inspection_take_it_on_their_shelf(self):
        from apps.inventory.models import Warehouse
        from apps.inventory.reports import reconcile_to_ledger

        self.first_in_first_out()
        inspection = Warehouse.objects.create(code="QA", name="Inspection", is_quarantine=True)
        order = self.make_order("10", "5")
        self.receipt = GoodsReceipt.objects.create(purchase_order=order,
                                                   receipt_date=datetime.date(2026, 1, 5))
        GoodsReceiptLine.objects.create(receipt=self.receipt, order_line=order.lines.first(),
                                        warehouse=inspection, quantity_received=Decimal("10"))
        self.receipt.post()
        self.receipt.accept(self.warehouse)
        self.landed()
        self.assertEqual((self.item.stock_value_at(self.warehouse), self.item.stock_value_at(inspection)),
                         (Decimal("130.00"), Decimal("0.00")))
        self.assertEqual(reconcile_to_ledger()["difference"], Decimal("0.00"))

    def written_off_but_one(self):
        self.written_off_leaving("1")

    def written_off_leaving(self, left):
        from apps.inventory.models import AdjustmentReason, StockAdjustment, StockAdjustmentLine

        reason = AdjustmentReason.objects.create(code="SCRAP", name="Scrapped", account=self.expense)
        adjustment = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 1, 20),
                                                    warehouse=self.warehouse, reason=reason)
        StockAdjustmentLine.objects.create(adjustment=adjustment, item=self.item, uom=self.uom,
                                           quantity=Decimal(left) - Decimal("10"))
        adjustment.post()

    # Released after eight of the ten went: the 80 came off the two left,
    # worth 26, and priced them at minus 27. The two carry 16 of it; the
    # eight took 64 with them, and that comes back out of cost of sales.

    def released_after_eight_of_ten_went(self):
        order, self.receipt = self.goods_received("10", "5")
        application = self.landed()
        self.written_off_leaving("2")
        application.release()

    def test_released_after_eight_went_the_two_left_give_back_their_share(self):
        self.released_after_eight_of_ten_went()
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("10.00"))
        # Eight written off at 13 (104 to the reason's account); released, the
        # two left give back 16 and the 64 comes back off that account (R1): 40.
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs),
                          self.balance(self.freight_expense), self.balance(self.expense)),
                         (Decimal("10.00"), Decimal("0"), Decimal("80.00"), Decimal("40.00")))

    def test_under_fifo_the_two_left_of_the_layer_give_back_their_share(self):
        from apps.inventory.reports import reconcile_to_ledger

        self.first_in_first_out()
        self.released_after_eight_of_ten_went()
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("10.00"))
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs), self.balance(self.expense)),
                         (Decimal("10.00"), Decimal("0"), Decimal("40.00")))
        self.assertEqual(reconcile_to_ledger()["difference"], Decimal("0.00"))


    # O2, O42, O48: landed cost follows the goods it landed on, still on the
    # shelf, moved since, sold, or issued to production (the owner's decision
    # of 9 October: freight on goods issued to a run is production cost).

    def with_its_freight(self):
        bill = self.order_for_receipt.create_bill(self.payable, bill_date=datetime.date(2026, 1, 25))
        BillLine.objects.create(bill=bill, charge=self.freight, description="Freight on this lot",
                                quantity=Decimal("1"), unit_price=Decimal("80"),
                                expense_account=self.freight_expense)
        bill.post()
        return bill

    def received(self):
        self.order_for_receipt, self.receipt = self.goods_received("10", "5")

    def issued_to_a_run(self, quantity):
        from apps.manufacturing.bom import BillOfMaterials, BomComponent
        from apps.inventory.models import Item
        from apps.manufacturing.orders import (
            ManufacturingSettings, MaterialIssue, MaterialIssueLine, WorkCentre, WorkOrder,
        )

        self.wip = Account.objects.create(code="1250", name="Work in progress", account_type=AccountType.ASSET)
        ManufacturingSettings.objects.create(wip_account=self.wip, variance_account=self.cogs)
        sack = Item.objects.create(sku="SACK", name="Sack", uom=self.uom)
        bom = BillOfMaterials.objects.create(item=sack, name="Sack", quantity_produced=Decimal("10"), uom=self.uom)
        BomComponent.objects.create(bom=bom, item=self.item, quantity=Decimal("10"), uom=self.uom, line_number=1)
        run = WorkOrder.objects.create(item=sack, bom=bom, quantity_ordered=Decimal("10"), uom=self.uom,
                                       warehouse=self.warehouse,
                                       work_centre=WorkCentre.objects.create(code="LOOM", name="Loom"))
        run.release(datetime.date(2026, 1, 15))
        issue = MaterialIssue.objects.create(work_order=run, issue_date=datetime.date(2026, 1, 15),
                                             warehouse=self.warehouse)
        MaterialIssueLine.objects.create(issue=issue, item=self.item, quantity=Decimal(quantity),
                                         uom=self.uom, line_number=1)
        issue.post()
        return run

    def test_released_after_the_put_away_it_comes_off_the_stock_room(self):
        # O42: landed on the bay, put away, released: the bay gave up 0, cost
        # of sales was credited 80, and the stock room kept the 80.
        self.via_the_bay()
        self.received()
        application = self.landed()
        self.assertEqual(self.item.stock_value_at(self.bay), Decimal("130.00"))
        self.put_away()
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("130.00"))
        application.release()
        self.assertEqual((self.item.stock_value_at(self.warehouse), self.item.stock_value_at(self.bay)),
                         (Decimal("50.00"), Decimal("0.00")))
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs), self.balance(self.freight_expense)),
                         (Decimal("50"), Decimal("0"), Decimal("80")))

    def test_its_own_bills_freight_on_goods_written_off_goes_where_the_write_off_posted(self):
        # O2: the bill's own freight debited inventory whatever had happened to
        # the goods. Written off (50 to the reason's account), the 80 follows
        # them there (R1): 130, cost of sales 0.
        self.received()
        self.written_off()
        self.with_its_freight()
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs), self.balance(self.expense)),
                         (Decimal("0"), Decimal("0"), Decimal("130")))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("0.00"))

    def test_its_own_bills_freight_lands_on_the_bay_the_goods_wait_in(self):
        # O2: it landed on the stock room they were bound for, empty.
        self.via_the_bay()
        self.received()
        self.with_its_freight()
        self.assertEqual((self.item.stock_value_at(self.bay), self.item.stock_value_at(self.warehouse)),
                         (Decimal("130.00"), Decimal("0.00")))

    def test_freight_on_goods_issued_to_a_run_is_production_cost(self):
        # O48: 6 of 10 issued to a run (30 into WIP), then 80 of freight: 32 on
        # the 4 on the shelf, 48 to the run, none to cost of sales.
        self.received()
        run = self.issued_to_a_run("6")
        application = self.landed()
        self.assertEqual((self.balance(self.inventory), self.balance(self.wip), self.balance(self.cogs)),
                         (Decimal("52.00"), Decimal("78.00"), Decimal("0")))
        self.assertEqual(run.material_cost(), Decimal("78.00"))
        application.release()
        self.assertEqual((self.balance(self.inventory), self.balance(self.wip), self.balance(self.cogs)),
                         (Decimal("20.00"), Decimal("30.00"), Decimal("0")))
        self.assertEqual(run.material_cost(), Decimal("30.00"))

    def via_inspection(self):
        from apps.inventory.models import Warehouse

        self.bay = Warehouse.objects.create(code="BAY", name="Receiving bay")
        self.inspection = Warehouse.objects.create(code="QC", name="Inspection", is_quarantine=True)
        self.warehouse.receipt_route = "inspect"
        self.warehouse.input_warehouse = self.bay
        self.warehouse.quality_warehouse = self.inspection
        self.warehouse.save()

    def inspected_and_cleared(self):
        self.receipt.lines.get().advance()
        self.assertEqual(self.item.on_hand_at(self.inspection), Decimal("10"))
        self.receipt.accept(self.warehouse)

    def test_released_after_the_goods_were_cleared_out_of_inspection_it_comes_off_the_stock_room(self):
        # O210: landed on the goods in inspection, cleared, released. The
        # clearance was not followed: cost of sales was credited 80 and the
        # stock room kept 130 (calc_fix_stores/o210.py, A).
        self.via_inspection()
        self.received()
        self.receipt.lines.get().advance()
        application = self.landed()
        self.assertEqual(self.item.stock_value_at(self.inspection), Decimal("130.00"))
        self.receipt.accept(self.warehouse)
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("130.00"))
        application.release()
        self.assertEqual((self.item.stock_value_at(self.warehouse), self.item.stock_value_at(self.inspection)),
                         (Decimal("50.00"), Decimal("0.00")))
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs), self.balance(self.freight_expense)),
                         (Decimal("50"), Decimal("0"), Decimal("80")))

    def test_freight_on_goods_cleared_out_of_inspection_and_issued_to_a_run_is_production_cost(self):
        # O210: cleared, 6 of 10 issued (30 into WIP), then 80 of freight: 32
        # on the 4 left, 48 to the run. Not followed past the clearance, the
        # 48 went to cost of sales (calc_fix_stores/o210.py, B).
        from apps.inventory.reports import reconcile_to_ledger

        self.via_inspection()
        self.received()
        self.inspected_and_cleared()
        run = self.issued_to_a_run("6")
        application = self.landed()
        self.assertEqual((self.balance(self.inventory), self.balance(self.wip), self.balance(self.cogs)),
                         (Decimal("52.00"), Decimal("78.00"), Decimal("0")))
        self.assertEqual(run.material_cost(), Decimal("78.00"))
        self.assertEqual(reconcile_to_ledger()["difference"], Decimal("0.00"))
        application.release()
        self.assertEqual((self.balance(self.inventory), self.balance(self.wip), self.balance(self.cogs)),
                         (Decimal("20.00"), Decimal("30.00"), Decimal("0")))
        self.assertEqual(reconcile_to_ledger()["difference"], Decimal("0.00"))

    def test_released_after_the_goods_were_rebatched_it_comes_off_the_batches_made(self):
        # Seen #9, end to end: ten at 5 received as L-1, 80 of freight landed on them, then L-1
        # split into L-2 (6) and L-3 (4). Released, the 80 comes off L-2 and L-3, not out of cost of
        # sales while they keep it, and the shelf foots to the ledger: 50 on each.
        from apps.inventory.models import Item, Lot
        from apps.inventory.reports import reconcile_to_ledger
        from apps.manufacturing.rebatch import rebatch

        Item.objects.filter(pk=self.item.pk).update(tracking="lot")
        self.item.refresh_from_db()
        first = Lot.objects.create(item=self.item, code="L-1")
        order = self.make_order("10", "5")
        self.receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 1, 5))
        GoodsReceiptLine.objects.create(receipt=self.receipt, order_line=order.lines.first(), lot=first,
                                        warehouse=self.warehouse, quantity_received=Decimal("10"))
        self.receipt.post()
        application = self.landed()
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("130.00"))
        six, four = (Lot.objects.create(item=self.item, code=code) for code in ("L-2", "L-3"))
        rebatch(self.item, self.warehouse, [(first, Decimal("10"))], [(six, Decimal("6")), (four, Decimal("4"))],
                "Re-sorted by width", on_date=datetime.date(2026, 2, 5))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("130.00"))
        application.release()
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("50.00"))
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs), self.balance(self.freight_expense)),
                         (Decimal("50"), Decimal("0"), Decimal("80")))
        self.assertEqual(reconcile_to_ledger()["difference"], Decimal("0.00"))

    def test_its_own_bills_freight_on_goods_issued_to_a_run_is_production_cost(self):
        self.received()
        self.issued_to_a_run("6")
        self.with_its_freight()
        self.assertEqual((self.balance(self.inventory), self.balance(self.wip), self.balance(self.cogs)),
                         (Decimal("52.00"), Decimal("78.00"), Decimal("0")))


    # The reverse of a bill's own landed freight: a debit note gives it back
    # off where the bill landed it, followed wherever the goods went since
    # (calc_fix_cost/given_back.py). Off the receipt's destination, a refund
    # after a put-away left the stock room at -80.

    def test_a_whole_bill_refunded_after_the_put_away_puts_every_account_back(self):
        self.via_the_bay()
        self.received()
        before = (self.balance(self.inventory), self.balance(self.freight_expense), self.balance(self.grni))
        bill = self.with_its_freight()
        self.put_away()
        bill.create_debit_note(memo="Refunded")
        self.assertEqual((self.item.stock_value_at(self.warehouse), self.item.stock_value_at(self.bay)),
                         (Decimal("50.00"), Decimal("0.00")))
        self.assertEqual((self.balance(self.inventory), self.balance(self.freight_expense), self.balance(self.grni)),
                         before)
        self.assertEqual(before, (Decimal("50"), Decimal("0"), Decimal("-50")))

    def test_refunded_while_the_goods_wait_in_the_bay_it_comes_off_the_bay(self):
        # Landed on the bay (130), refunded before the put-away: given back off
        # the stock room the receipt was bound for, the room read -80 and the bay 130.
        self.via_the_bay()
        self.received()
        bill = self.with_its_freight()
        self.freight_refunded(bill)
        self.assertEqual((self.item.stock_value_at(self.bay), self.item.stock_value_at(self.warehouse)),
                         (Decimal("50.00"), Decimal("0.00")))
        self.put_away("6")
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("30.00"))

    def freight_refunded(self, bill):
        freight = bill.lines.get(charge=self.freight)
        return bill.create_debit_note(memo="Refunded", quantities={freight: Decimal("1")})

    def test_refunded_after_the_goods_went_into_a_run_the_run_gives_it_back(self):
        self.received()
        run = self.issued_to_a_run("6")
        bill = self.with_its_freight()
        self.freight_refunded(bill)
        self.assertEqual((self.balance(self.inventory), self.balance(self.wip), self.balance(self.cogs)),
                         (Decimal("20.00"), Decimal("30.00"), Decimal("0")))
        self.assertEqual(run.material_cost(), Decimal("30.00"))

    def test_refunded_after_the_goods_were_written_off_the_write_offs_account_gives_it_back(self):
        # Landed (130), all ten written off at 130 to the reason's account,
        # the freight refunded: that account gives back the 80 (R1): 50 left.
        self.received()
        bill = self.with_its_freight()
        self.written_off()
        self.freight_refunded(bill)
        self.assertEqual((self.balance(self.inventory), self.balance(self.cogs), self.balance(self.expense),
                          self.item.stock_value_at(self.warehouse)),
                         (Decimal("0"), Decimal("0"), Decimal("50"), Decimal("0.00")))

    def test_refunded_after_the_goods_went_back_the_returns_variance_gives_it_back(self):
        self.received()
        bill = self.with_its_freight()
        self.receipt.create_return(debit_bills=False)
        self.assertEqual(self.balance(self.ppv), Decimal("80"))
        self.freight_refunded(bill)
        self.assertEqual((self.balance(self.ppv), self.balance(self.inventory), self.balance(self.cogs)),
                         (Decimal("0"), Decimal("0"), Decimal("0")))


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

    def test_a_refund_drafted_before_the_capitalising_leaves_nothing_to_capitalise(self):
        # O353: the draft waiting gives the whole charge back, so capitalising it is refused and the draft
        # posts. Before, the charge was capitalised and the draft could then never post.
        bill, charge = self.carrier_bill("80")
        note = self.drafted_refund(bill, charge)
        with self.assertRaisesMessage(ValidationError, "nothing to capitalise"):
            charge.capitalise_as_asset(self.category)
        note.post()
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))

    def test_a_refund_drafted_after_the_capitalising_is_refused(self):
        bill, charge = self.carrier_bill("80")
        charge.capitalise_as_asset(self.category)
        with self.assertRaisesMessage(ValidationError, "was capitalised as fixed assets"):
            self.drafted_refund(bill, charge)

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


# The purchasing audit probes (docs/handoff/reports/probes/audit_pur_purchasing_tests_probe_audit.py).
JAN = lambda day: datetime.date(2026, 1, day)  # noqa: E731


def probe_accounts(self):
    """What every probe adds to the lifecycle fixture: variance and exchange accounts, tolerance 0."""
    acc = lambda code, name, kind: Account.objects.create(code=code, name=name, account_type=kind)  # noqa: E731
    self.ppv = acc("5900", "Purchase price variance", AccountType.EXPENSE)
    self.fx_loss = acc("7100", "FX loss", AccountType.EXPENSE)
    self.fx_gain = acc("7000", "FX gain", AccountType.INCOME)
    company = Company.get()
    company.purchase_price_variance_account = self.ppv
    company.default_purchase_expense_account = self.expense
    company.fx_loss_account = self.fx_loss
    company.fx_gain_account = self.fx_gain
    company.purchase_price_tolerance_percent = Decimal("0")
    company.save()


class ProbeCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        probe_accounts(self)

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            d=Sum("debit"), c=Sum("credit"))
        return (rows["d"] or Decimal("0")) - (rows["c"] or Decimal("0"))

    def order(self, quantity="10", price="5", discount="0", policy=BillPolicy.RECEIVED, vendor=None,
              item=None, uom=None, confirm=True):
        order = PurchaseOrder.objects.create(vendor=vendor or self.vendor, order_date=JAN(1),
                                             bill_policy=policy)
        PurchaseOrderLine.objects.create(order=order, item=item or self.item, uom=uom or self.uom,
                                         quantity=Decimal(quantity), unit_price=Decimal(price),
                                         discount_percent=Decimal(discount))
        if confirm:
            order.confirm()
        return order

    def hand_bill(self, line, quantity, price, discount="0", vendor=None, order=None, currency=None,
                  reference="", item=None, link=True, day=None):
        bill = Bill.objects.create(vendor=vendor or self.vendor, bill_date=day or JAN(10), purchase_order=order,
                                   payable_account=self.payable, currency=currency or self.usd,
                                   reference=reference)
        BillLine.objects.create(bill=bill, order_line=line if link else None, item=item or self.item,
                                quantity=Decimal(quantity), unit_price=Decimal(price),
                                discount_percent=Decimal(discount), expense_account=self.expense)
        return bill

    def receive_on(self, order, quantity, day):
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=day)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(), warehouse=self.warehouse,
                                        quantity_received=Decimal(quantity))
        receipt.post()
        return receipt

    def other_vendor(self):
        other = Party.objects.create(code="V-2", name="Another supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        return other


class GrniClearsTests(ProbeCase):
    """O89, O93, O94: the purchasing audit probes of 9 October, kept as regression tests."""

    def test_billed_ahead_of_its_goods_grni_clears_when_they_come(self):
        """Billed as ordered, 10 @ 5, then the 10 arrive: GRNI 0, purchases 0, inventory 50."""
        order = self.order(policy=BillPolicy.ORDERED)
        order.create_bill(self.payable, bill_date=JAN(3)).post()
        self.receive(order, "10")
        observed = (self.balance(self.grni), self.balance(self.expense), self.balance(self.inventory))
        self.assertEqual(observed, (Decimal("0"), Decimal("0"), Decimal("50.00")),
                         "(GRNI, purchases, inventory)")

    def test_billed_one_at_a_time_grni_clears_to_the_paisa(self):
        """3 @ 10.10 less 2.5% received (accrued 29.54), billed 1+1+1 at 9.85: GRNI 0.00."""
        order = self.order("3", "10.10", discount="2.5")
        self.receive(order, "3")
        line = order.lines.get()
        for _ in range(3):
            self.hand_bill(line, "1", "10.10", discount="2.5", order=order).post()
        self.assertEqual(self.balance(self.grni), Decimal("0.00"))

    def test_every_variation_at_once_grni_clears(self):
        """
        EUR 10 @ 5 less 10%: 6 in at 90, 4 in at 92 (2,430 + 1,656); billed 10 @ 5.20 less 10% at 95;
        3 of the first receipt sent back, debiting the bill. GRNI 0; the shelf gives up 3 at its
        average 408.60 (1,225.80, 10.80 of it price variance), leaving inventory 2,860.20.
        """
        eur = Currency.objects.create(code="EUR", name="Euro")
        for day, rate in ((1, "90"), (6, "92"), (10, "95")):
            ExchangeRate.objects.create(currency=eur, rate=Decimal(rate), valid_from=JAN(day))
        company = Company.get()
        company.purchase_price_tolerance_percent = Decimal("10")
        company.save()
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN(1), currency=eur)
        line = PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                                unit_price=Decimal("5"), discount_percent=Decimal("10"))
        order.confirm()
        receipts = []
        for quantity, day in (("6", 5), ("4", 7)):
            receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=JAN(day))
            GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, warehouse=self.warehouse,
                                            quantity_received=Decimal(quantity))
            receipt.post()
            receipts.append(receipt)
        self.hand_bill(line, "10", "5.20", discount="10", order=order, currency=eur).post()
        receipts[0].create_return({receipts[0].lines.get(): Decimal("3")})
        self.assertEqual((self.balance(self.grni), self.balance(self.inventory)),
                         (Decimal("0"), Decimal("2860.20")), "(GRNI, inventory)")
    def test_goods_sent_back_are_debited_on_the_bill_that_paid_for_them(self):
        """
        20 ordered @ 5 (tolerance 10%). 10 in on 5 Jan, billed 10 Jan at 5.00; 10 in on 11 Jan, billed
        12 Jan at 5.40 (4.00 variance). 3 of the second receipt sent back: the bill for them gave 16.20,
        so the vendor is debited 16.20 and the variance kept is 2.80.
        """
        company = Company.get()
        company.purchase_price_tolerance_percent = Decimal("10")
        company.save()
        order = self.order("20", "5")
        line = order.lines.get()
        self.receive_on(order, "10", JAN(5))
        self.hand_bill(line, "10", "5.00", order=order, day=JAN(10)).post()
        second = self.receive_on(order, "10", JAN(11))
        self.hand_bill(line, "10", "5.40", order=order, day=JAN(12)).post()
        returned = second.create_return({second.lines.get(): Decimal("3")})
        debited = sum((note.total() for note in returned.debit_notes_created), Decimal("0"))
        self.assertEqual((debited, self.balance(self.ppv), self.balance(self.grni)),
                         (Decimal("16.20"), Decimal("2.80"), Decimal("0")), "(debited, PPV, GRNI)")


class CapitaliseFromStockTests(ProbeCase):
    """O101: capitalising a received stock line takes it off the shelf as well as GRNI."""

    def test_capitalising_a_received_machine_leaves_grni_and_stock_clear(self):
        """A stocked machine 1 @ 12,000 received and billed, then capitalised: GRNI 0, inventory 0, plant 12,000."""
        from apps.assets.models import AssetCategory

        acc = lambda code, name, kind: Account.objects.create(code=code, name=name, account_type=kind)  # noqa: E731
        plant = acc("1500", "Plant", AccountType.ASSET)
        category = AssetCategory.objects.create(
            code="PLANT", name="Plant", asset_account=plant,
            accumulated_account=acc("1590", "Accumulated", AccountType.ASSET),
            expense_account=acc("6100", "Depreciation", AccountType.EXPENSE),
            disposal_account=acc("6200", "Disposal", AccountType.EXPENSE), default_life_months=60)
        order = self.order("1", "12000")
        self.receive(order, "1")
        bill = order.create_bill(self.payable, bill_date=JAN(10))
        bill.post()
        (asset,) = bill.lines.get().capitalise_as_asset(category)
        observed = (self.balance(self.grni), self.balance(self.inventory), self.balance(plant))
        self.assertEqual(observed, (Decimal("0"), Decimal("0"), Decimal("12000.00")), "(GRNI, inventory, plant)")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("0"))
        # Undone, it is back on the shelf at what it left at, and off plant.
        asset.uncapitalise()
        self.assertEqual((self.balance(self.inventory), self.balance(plant), self.item.on_hand_at(self.warehouse)),
                         (Decimal("12000.00"), Decimal("0"), Decimal("1")))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("12000.00"))


class ADebitNoteKeepsTheCostCentreTests(ProbeCase):
    """O56 (audit_acc CostCentreProbe): 10 x 100 to the loom shed, 3 debited back: LOOM 700, nothing unallocated."""

    def test_a_debit_note_takes_the_cost_off_the_centre_that_bore_it(self):
        from apps.accounting.analytic import CostCentre, costs_by_centre

        loom = CostCentre.objects.create(code="LOOM", name="Loom shed")
        bill = Bill.objects.create(vendor=self.vendor, bill_date=JAN(10), payable_account=self.payable,
                                   currency=self.usd)
        BillLine.objects.create(bill=bill, description="Spares", quantity=Decimal("10"), unit_price=Decimal("100"),
                                expense_account=self.expense, cost_centre=loom)
        bill.post()
        bill.create_debit_note(quantities={bill.lines.get(): Decimal("3")})
        rows = {row["code"] or "unallocated": row["amount"] for row in costs_by_centre()["rows"]}
        self.assertEqual(rows, {"LOOM": Decimal("700.00")})


class ThePriceLegReadsTheDiscountTests(ProbeCase):
    """O91. Agreed 10 @ 100 less 10% (900); tolerance 0."""

    def test_a_bill_at_the_gross_price_without_the_agreed_discount_is_refused(self):
        order = self.order("10", "100", discount="10")
        self.receive(order, "10")
        bill = self.hand_bill(order.lines.get(), "10", "100", order=order)
        with self.assertRaisesMessage(ValidationError, "ordered at 100 less 10% (90 a unit) but billed at 100"):
            bill.post()
        self.assertEqual(self.balance(self.ppv), Decimal("0"))

    def test_a_bill_at_the_agreed_net_posts_with_no_variance_however_it_is_written(self):
        order = self.order("10", "100", discount="10")
        self.receive(order, "10")
        self.hand_bill(order.lines.get(), "5", "100", discount="10", order=order).post()
        self.hand_bill(order.lines.get(), "5", "90", order=order).post()
        self.assertEqual(self.balance(self.ppv), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-900.00"))


class OneVendorInvoiceInItsYearTests(ProbeCase):
    """O95. INV-77 on file for V-1, billed 10 January 2026 (the year from 1 April 2025)."""

    def setUp(self):
        super().setUp()
        self.po = self.order()
        self.receive(self.po, "10")
        self.first = self.hand_bill(self.po.lines.get(), "2", "5", order=self.po, reference="INV-77")
        self.first.post()

    def test_the_same_number_in_another_case_spacing_or_dash_is_refused(self):
        # The g22 decision: case, whitespace, Unicode forms and dashes fold; separators do not.
        for written in ("inv-77", " INV-77 ", "Inv\u2010" "77", "INV\u221277", "\uff29\uff2e\uff36-\uff17\uff17",
                        ):
            with self.subTest(written=written):
                with self.assertRaisesMessage(ValidationError, f"invoice '{written}' is already on file as 'INV-77' as "
                                                               f"{self.first}, dated 2026-01-10"):
                    self.hand_bill(self.po.lines.get(), "2", "5", order=self.po, reference=written)
        self.assertEqual(self.balance(self.payable), Decimal("-10.00"))

    def test_the_number_again_in_the_next_year_or_from_another_vendor_stands(self):
        self.hand_bill(self.po.lines.get(), "2", "5", order=self.po, reference="INV-77", day=datetime.date(2026, 4, 1)).post()
        other = self.other_vendor()
        Bill.objects.create(vendor=other, bill_date=JAN(10), payable_account=self.payable, currency=self.usd,
                            reference="INV-77")
        self.assertEqual(Bill.objects.filter(reference_key="2025:INV-77").count(), 2)
        self.assertEqual(Bill.objects.filter(reference_key="2026:INV-77").count(), 1)

    def test_a_distinct_number_stands(self):
        self.hand_bill(self.po.lines.get(), "2", "5", order=self.po, reference="INV-771").post()
        self.assertEqual(self.balance(self.payable), Decimal("-20.00"))

    def test_another_separator_is_another_invoice(self):
        """"INV-77", "INV/77" and "INV77" are three invoices; "X/1/12" and "X/11/2" two (O178)."""
        for written in ("INV/77", "INV77", "INV 77", "X/1/12", "X/11/2"):
            self.hand_bill(self.po.lines.get(), "1", "5", order=self.po, reference=written).post()
        self.assertEqual(self.balance(self.payable), Decimal("-35.00"))

    def test_a_placeholder_or_an_overlong_number_is_refused_in_words(self):
        for written, said in (("-", "has no letter or digit"), ("./-", "has no letter or digit"),
                              ("\ufb03" * 30, "at most 64")):
            with self.subTest(written=written), self.assertRaisesMessage(ValidationError, said):
                self.hand_bill(self.po.lines.get(), "1", "5", order=self.po, reference=written)


class AReturnsDebitNoteRecordsTheSuppliersCreditNoteTests(ProbeCase):
    """
    O154. 10 @ 5 received and billed (50); 4 go back with debit_bills=True,
    which posts the debit note (20) at once. The supplier's CN-41 arrives a
    week later: the AP Manager records it once; it is not changed after.
    """

    def test_recorded_once_after_posting_by_whoever_posts_bills(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        order = self.order()
        receipt = self.receive(order, "10")
        self.hand_bill(order.lines.get(), "10", "5", order=order, reference="INV-1").post()
        receipt.create_return(quantities={receipt.lines.get(): Decimal("4")}, debit_bills=True)
        note = Bill.objects.get(debits__isnull=False)
        self.assertEqual((note.posted, note.total(), note.supplier_note_number), (True, Decimal("20.00"), ""))

        call_command("setup_roles", verbosity=0)
        ap, clerk = User.objects.create_user("ap"), User.objects.create_user("clerk")
        ap.groups.add(Group.objects.get(name="AP Manager"))
        clerk.groups.add(Group.objects.get(name="Purchasing Clerk"))
        url = f"/api/purchasing/bills/{note.pk}/supplier_note/"
        given = {"supplier_note_number": "CN-41", "supplier_note_date": str(JAN(17))}
        as_clerk, as_ap = APIClient(), APIClient()
        as_clerk.force_authenticate(clerk)
        as_ap.force_authenticate(ap)
        self.assertEqual(as_clerk.post(url, given, format="json").status_code, 403)
        recorded = as_ap.post(url, given, format="json")
        self.assertEqual(recorded.status_code, 200, recorded.content)
        again = as_ap.post(url, {**given, "supplier_note_number": "CN-42"}, format="json")
        self.assertEqual(again.status_code, 400, again.content)
        self.assertIn("records V-1 - Supplier's credit note CN-41 dated 2026-01-17", str(again.json()))
        note = Bill.objects.get(pk=note.pk)
        self.assertEqual((note.supplier_note_number, note.supplier_note_date, note.total()),
                         ("CN-41", JAN(17), Decimal("20.00")))


class AnUnkeyedNoteEditedAsADraftTests(ProbeCase):
    """
    O190. DN-1 answers V-1's CN-9; a draft note recording "cn-9", left
    unkeyed by migration 0063 (a collision), is edited over the API: refused
    in words naming DN-1, not by the constraint.
    """

    def test_refused_naming_the_note_that_answers_it(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        order = self.order()
        self.receive(order, "10")
        bill = self.hand_bill(order.lines.get(), "10", "5", order=order, reference="INV-1")
        bill.post()
        first = bill.create_debit_note(quantities={bill.lines.get(): Decimal("1")}, supplier_note_number="CN-9",
                                       supplier_note_date=JAN(12))
        draft = Bill.objects.create(vendor=self.vendor, bill_date=JAN(13), payable_account=self.payable,
                                    currency=self.usd, debits=bill)
        Bill.objects.filter(pk=draft.pk).update(supplier_note_number="cn-9", supplier_note_key="")  # as 0063 left it
        call_command("setup_roles", verbosity=0)
        ap = User.objects.create_user("ap")
        ap.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(ap)
        answer = client.patch(f"/api/purchasing/bills/{draft.pk}/", {"supplier_note_date": str(JAN(14))}, format="json")
        self.assertEqual(answer.status_code, 400, answer.content)
        self.assertIn(f"{first} already answers V-1 - Supplier's credit note CN-9; one credit note is answered by one "
                      "debit note.", str(answer.json()["supplier_note_number"]))


class AReturnBeforeTheBillsTests(ProbeCase):
    """
    O211 (calc_fix_purch/o211.py). 15 ordered at 5. R1 brings 10 and 6 of them
    go back before anything is billed; R2 brings 5. B1 bills R1's 4 at 5.00,
    B2 bills R2's 5 at 4.80. Two of R2 then go back: B2 paid for them, so B2
    is debited, 2 x 4.80 = 9.60, not B1 at 10.00.
    """

    def test_a_later_return_finds_the_bill_that_paid_for_its_receipt(self):
        order = self.order("15", "5")
        first = self.receive_on(order, "10", JAN(2))
        first.create_return(quantities={first.lines.get(): Decimal("6")}, debit_bills=True)
        second = self.receive_on(order, "5", JAN(3))
        b1 = self.hand_bill(order.lines.get(), "4", "5", order=order, reference="B-1")
        b1.post()
        b2 = self.hand_bill(order.lines.get(), "5", "4.80", order=order, reference="B-2")
        b2.post()
        second.create_return(quantities={second.lines.get(): Decimal("2")}, debit_bills=True)
        notes = [(note.debits_id, note.total()) for note in Bill.objects.filter(debits__isnull=False)]
        self.assertEqual(notes, [(b2.pk, Decimal("9.60"))])


class AReturnsNoteIsDatedOnTheReturnsDayTests(ProbeCase):
    """
    Integrator's item B (g22): 10 received 2 January, billed 3 January; 4 go
    back on 20 January, recorded later. The return and its debit note are
    dated 20 January, in January's period and GST month, not on the day
    the clerk keys it.
    """

    def test_the_note_takes_the_returns_day_and_never_one_before_the_bill(self):
        order = self.order()
        receipt = self.receive_on(order, "10", JAN(2))
        self.hand_bill(order.lines.get(), "10", "5", order=order, reference="B-1", day=JAN(3)).post()
        with self.assertRaisesMessage(ValidationError, "is not returned on 2026-01-01: the goods came in on 2026-01-02"):
            receipt.create_return(quantities={receipt.lines.get(): Decimal("4")}, on_date=JAN(1))
        back = receipt.create_return(quantities={receipt.lines.get(): Decimal("4")}, on_date=JAN(20))
        note = Bill.objects.get(debits__isnull=False)
        self.assertEqual((back.receipt_date, note.bill_date, note.journal_entry.date, note.total()),
                         (JAN(20), JAN(20), JAN(20), Decimal("20.00")))
