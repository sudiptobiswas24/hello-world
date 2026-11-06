"""
Moving stock between warehouses.

TRANSFER_IN and TRANSFER_OUT existed from the start; only purchasing
ever wrote them. Inventory could not move a pallet from one site to
another.

The invariant every test here comes back to: a transfer moves stock
between shelves and must not move value. Company-wide stock value before
and after — including after a cancellation — has to be the same number.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)

from .models import (
    Item,
    ItemType,
    MovementType,
    StockMovement,
    StockTransfer,
    StockTransferLine,
    TransferStatus,
    Warehouse,
)


class TransferTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        self.case = UnitOfMeasure.objects.create(
            code="case", name="Case of 12", category=UnitOfMeasureCategory.COUNT,
            base_unit=self.each, conversion_factor=Decimal("12"),
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory, default_cogs_account=self.cogs,
        )
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.each)
        self.north = Warehouse.objects.create(code="N", name="North")
        self.south = Warehouse.objects.create(code="S", name="South")
        self.transit = Warehouse.objects.create(code="T", name="On the road", is_transit=True)

    def stock(self, quantity="100", cost="5", warehouse=None, item=None):
        return StockMovement.objects.create(
            item=item or self.item, warehouse=warehouse or self.north,
            movement_type=MovementType.RECEIPT, uom=(item or self.item).uom,
            quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )

    def exact_value(self, item=None):
        """Company-wide stock value, unrounded."""
        item = item or self.item
        return sum(
            (item.valuation_at(w)[1] for w in Warehouse.objects.all()), Decimal("0")
        )

    def company_value(self, item=None):
        item = item or self.item
        return sum(
            (item.valuation_at(w)[1] for w in Warehouse.objects.all()), Decimal("0")
        ).quantize(Decimal("0.01"))

    def transfer(self, quantity="30", uom=None, transit=False, to=None, source=None):
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=source or self.north,
            to_warehouse=to or self.south,
            transit_warehouse=self.transit if transit else None,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.item, uom=uom or self.each, quantity=Decimal(quantity)
        )
        return move


class DirectTransferTests(TransferTestCase):
    def test_it_moves_the_quantity(self):
        self.stock("100", "5")
        self.transfer("30").post()
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("70"))
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("30"))

    def test_it_moves_the_value_with_it(self):
        self.stock("100", "5")
        self.transfer("30").post()
        self.assertEqual(self.item.stock_value_at(self.north), Decimal("350.00"))
        self.assertEqual(self.item.stock_value_at(self.south), Decimal("150.00"))

    def test_the_company_is_worth_the_same_afterwards(self):
        self.stock("100", "5")
        before = self.company_value()
        self.transfer("30").post()
        self.assertEqual(self.company_value(), before)

    def test_an_uneven_average_still_leaves_the_total_alone(self):
        # 1000 over 300 is 3.3333..., which multiplied back is not 1000.
        self.stock("100", "4")
        self.stock("200", "3")
        before = self.company_value()
        self.transfer("70").post()
        self.assertEqual(self.company_value(), before)

    def test_a_transfer_carries_the_value_to_a_hundredth_of_a_cent(self):
        # 1000 over 300 is 3.3333..., and a unit cost has four places, so
        # seventy units of it cannot be expressed as quantity x cost. The
        # remainder rides along as a value adjustment. Rounding that
        # remainder to the cent, as a posted amount would be, drops a
        # fifth of a cent here — invisible once, and a real number after a
        # warehouse has been shuffling stock for a year. stock_value_at
        # rounds to the cent and would hide it, so this reads the
        # unrounded figure the replay actually carries.
        self.stock("100", "4")
        self.stock("200", "3")
        before = self.exact_value()
        self.transfer("70").post()
        self.assertLess(abs(self.exact_value() - before), Decimal("0.0001"))

    def test_shuttling_stock_back_and_forth_does_not_erode_it(self):
        self.stock("100", "4")
        self.stock("200", "3")
        before = self.exact_value()
        for _ in range(50):
            self.transfer("70").post()
            self.transfer("70", source=self.south, to=self.north).post()
        self.assertLess(abs(self.exact_value() - before), Decimal("0.01"))

    def test_it_posts_nothing_to_the_ledger(self):
        from apps.accounting.models import JournalEntry

        self.stock("100", "5")
        self.transfer("30").post()
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_it_takes_a_number(self):
        self.stock("100", "5")
        move = self.transfer("30")
        move.post()
        self.assertTrue(move.number.startswith("TRF-"))
        self.assertEqual(move.status, TransferStatus.RECEIVED)

    def test_a_line_written_in_cases_moves_twelve_times_as_much(self):
        self.stock("120", "5")
        self.transfer("2", uom=self.case).post()
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("24"))
        self.assertEqual(self.item.stock_value_at(self.south), Decimal("120.00"))

    def test_it_will_not_move_more_than_is_there(self):
        self.stock("10", "5")
        with self.assertRaises(ValidationError) as caught:
            self.transfer("11").post()
        self.assertIn("cannot move", str(caught.exception))

    def test_a_transfer_with_a_transit_warehouse_must_be_despatched(self):
        self.stock("100", "5")
        with self.assertRaises(ValidationError) as caught:
            self.transfer("30", transit=True).post()
        self.assertIn("Despatch it", str(caught.exception))


class TwoStepTransferTests(TransferTestCase):
    def test_despatching_takes_it_off_the_origin_shelf(self):
        self.stock("100", "5")
        move = self.transfer("30", transit=True)
        move.dispatch()
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("70"))
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("0"))
        self.assertEqual(move.status, TransferStatus.IN_TRANSIT)

    def test_stock_in_transit_is_owned_and_valued(self):
        self.stock("100", "5")
        self.transfer("30", transit=True).dispatch()
        self.assertEqual(self.item.on_hand_at(self.transit), Decimal("30"))
        self.assertEqual(self.item.stock_value_at(self.transit), Decimal("150.00"))

    def test_stock_in_transit_is_not_available_to_pick(self):
        self.stock("100", "5")
        self.transfer("30", transit=True).dispatch()
        self.assertEqual(self.item.available_at(self.transit), 0)

    def test_the_company_is_worth_the_same_mid_journey(self):
        self.stock("100", "5")
        before = self.company_value()
        self.transfer("30", transit=True).dispatch()
        self.assertEqual(self.company_value(), before)

    def test_receiving_lands_it_at_the_destination(self):
        self.stock("100", "5")
        move = self.transfer("30", transit=True)
        move.dispatch()
        move.receive()
        self.assertEqual(self.item.on_hand_at(self.transit), Decimal("0"))
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("30"))
        self.assertEqual(move.status, TransferStatus.RECEIVED)

    def test_a_lorry_that_arrives_short_leaves_the_rest_in_transit(self):
        self.stock("100", "5")
        move = self.transfer("30", transit=True)
        move.dispatch()
        line = move.lines.get()
        move.receive({line: Decimal("18")})
        move.refresh_from_db()
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("18"))
        self.assertEqual(self.item.on_hand_at(self.transit), Decimal("12"))
        self.assertEqual(move.status, TransferStatus.IN_TRANSIT)
        self.assertEqual(line.quantity_outstanding(), Decimal("12"))

    def test_the_rest_can_arrive_later(self):
        self.stock("100", "5")
        move = self.transfer("30", transit=True)
        move.dispatch()
        line = move.lines.get()
        move.receive({line: Decimal("18")})
        move.receive()
        move.refresh_from_db()
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("30"))
        self.assertEqual(move.status, TransferStatus.RECEIVED)

    def test_it_cannot_receive_more_than_left(self):
        self.stock("100", "5")
        move = self.transfer("30", transit=True)
        move.dispatch()
        with self.assertRaises(ValidationError) as caught:
            move.receive({move.lines.get(): Decimal("31")})
        self.assertIn("still in transit", str(caught.exception))

    def test_a_draft_cannot_be_received(self):
        self.stock("100", "5")
        move = self.transfer("30", transit=True)
        with self.assertRaises(ValidationError) as caught:
            move.receive()
        self.assertIn("in transit can be received", str(caught.exception))

    def test_the_transit_warehouse_must_say_it_is_one(self):
        self.stock("100", "5")
        siding = Warehouse.objects.create(code="X", name="Siding")
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.north, to_warehouse=self.south, transit_warehouse=siding,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.item, uom=self.each, quantity=Decimal("30")
        )
        with self.assertRaises(ValidationError) as caught:
            move.dispatch()
        self.assertIn("not marked as a transit warehouse", str(caught.exception))


class CancellingTests(TransferTestCase):
    def test_cancelling_a_direct_transfer_puts_it_back(self):
        self.stock("100", "5")
        move = self.transfer("30")
        move.post()
        move.cancel()
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("100"))
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("0"))
        self.assertEqual(move.status, TransferStatus.CANCELLED)

    def test_cancelling_leaves_the_value_where_it_started(self):
        self.stock("100", "5")
        move = self.transfer("30")
        move.post()
        move.cancel()
        self.assertEqual(self.item.stock_value_at(self.north), Decimal("500.00"))
        self.assertEqual(self.item.stock_value_at(self.south), Decimal("0.00"))

    def test_cancelling_survives_the_destination_average_moving(self):
        # The destination takes in cheaper stock while the transfer sits
        # there. Sending the units back at the destination's new average
        # would return less value than left, and the difference would
        # never clear.
        self.stock("100", "8")
        move = self.transfer("50")
        move.post()
        self.stock("50", "2", warehouse=self.south)
        before = self.company_value()
        move.cancel()
        self.assertEqual(self.company_value(), before)
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("100"))
        self.assertEqual(self.item.stock_value_at(self.north), Decimal("800.00"))

    def test_cancelling_a_lorry_in_transit_returns_it_to_the_origin(self):
        self.stock("100", "5")
        move = self.transfer("30", transit=True)
        move.dispatch()
        move.cancel()
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("100"))
        self.assertEqual(self.item.on_hand_at(self.transit), Decimal("0"))

    def test_cancelling_after_a_partial_receipt_unwinds_both_hops(self):
        self.stock("100", "5")
        move = self.transfer("30", transit=True)
        move.dispatch()
        move.receive({move.lines.get(): Decimal("18")})
        before = self.company_value()
        move.cancel()
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("100"))
        self.assertEqual(self.item.on_hand_at(self.transit), Decimal("0"))
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("0"))
        self.assertEqual(self.company_value(), before)

    def test_nothing_is_left_in_transit_after_a_cancellation(self):
        self.stock("100", "5")
        move = self.transfer("30", transit=True)
        move.dispatch()
        move.cancel()
        self.assertEqual(move.lines.get().quantity_outstanding(), Decimal("0"))

    def test_a_draft_has_moved_nothing_to_cancel(self):
        self.stock("100", "5")
        move = self.transfer("30")
        with self.assertRaises(ValidationError) as caught:
            move.cancel()
        self.assertIn("moved nothing", str(caught.exception))

    def test_it_cannot_be_cancelled_twice(self):
        self.stock("100", "5")
        move = self.transfer("30")
        move.post()
        move.cancel()
        with self.assertRaises(ValidationError):
            move.cancel()

    def test_cancelling_is_refused_when_the_stock_has_since_gone(self):
        self.stock("100", "5")
        move = self.transfer("30")
        move.post()
        StockMovement.objects.create(
            item=self.item, warehouse=self.south, movement_type=MovementType.ISSUE,
            uom=self.each, quantity=Decimal("-25"),
            unit_cost=self.item.average_cost_at(self.south), occurred_at=timezone.now(),
        )
        with self.assertRaises(ValidationError) as caught:
            move.cancel()
        self.assertIn("remains", str(caught.exception))


class TransferGuardTests(TransferTestCase):
    def test_a_transfer_that_goes_nowhere_is_refused(self):
        # Same warehouse is allowed now that shelf-to-shelf is a real
        # move, so the rule became "somewhere else" — which is a fact
        # about the lines and is checked when the stock moves.
        self.stock("100", "5")
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.north, to_warehouse=self.north,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.item, uom=self.each, quantity=Decimal("10")
        )
        with self.assertRaises(ValidationError) as caught:
            move.post()
        self.assertIn("end up where it started", str(caught.exception))

    def test_consignment_stock_is_not_the_companys_to_move(self):
        vendor = Party.objects.create(code="V", name="Supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        theirs = Warehouse.objects.create(code="C", name="Theirs", consignment_vendor=vendor)
        self.stock("100", "5", warehouse=theirs)
        with self.assertRaises(ValidationError) as caught:
            self.transfer("10", source=theirs).post()
        self.assertIn("not the company's to transfer", str(caught.exception))

    def test_quarantined_stock_must_be_accepted_before_it_moves(self):
        bay = Warehouse.objects.create(code="Q", name="Bay", is_quarantine=True)
        self.stock("100", "5", warehouse=bay)
        with self.assertRaises(ValidationError) as caught:
            self.transfer("10", source=bay).post()
        self.assertIn("awaiting inspection", str(caught.exception))

    def test_a_non_stocked_item_has_nothing_to_move(self):
        service = Item.objects.create(
            sku="SVC", name="Install", uom=self.each,
            item_type=ItemType.SERVICE, track_inventory=False,
        )
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.north, to_warehouse=self.south,
        )
        StockTransferLine.objects.create(
            transfer=move, item=service, uom=self.each, quantity=Decimal("1")
        )
        with self.assertRaises(ValidationError) as caught:
            move.post()
        self.assertIn("not stocked", str(caught.exception))

    def test_an_empty_transfer_moves_nothing(self):
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.north, to_warehouse=self.south,
        )
        with self.assertRaises(ValidationError) as caught:
            move.post()
        self.assertIn("no lines", str(caught.exception))

    def test_a_moved_transfer_cannot_be_edited(self):
        self.stock("100", "5")
        move = self.transfer("30")
        move.post()
        move.memo = "changed"
        with self.assertRaises(ValidationError):
            move.save()

    def test_a_line_on_a_moved_transfer_cannot_be_edited(self):
        self.stock("100", "5")
        move = self.transfer("30")
        move.post()
        line = move.lines.get()
        line.quantity = Decimal("40")
        with self.assertRaises(ValidationError):
            line.save()

    def test_it_cannot_be_posted_twice(self):
        self.stock("100", "5")
        move = self.transfer("30")
        move.post()
        with self.assertRaises(ValidationError):
            move.post()
