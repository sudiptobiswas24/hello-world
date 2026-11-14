"""
review_cost probes for gate g20 (landed cost forward and reverse, GRNI, returns, debit notes).

Hand figures: scratchpad/calc_review_cost/landed.py. Kept as regression tests for R1 to R8 (reports/review_cost.md), at the probes' own numbers.
"""

import datetime
from decimal import Decimal

from apps.accounting.models import Account, AccountType, ChargeType
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.inventory.reports import reconcile_to_ledger

from . import tests_returns_and_landed as base
from .models import Bill, BillLine, BillPolicy, GoodsReceipt, GoodsReceiptLine

D = Decimal
JAN = lambda day: datetime.date(2026, 1, day)  # noqa: E731


class Fates(base.ProbeCase):
    """10 at 5 into the bay, 80 of freight on the goods' own bill; the goods then meet every fate."""

    def setUp(self):
        super().setUp()
        from apps.inventory.models import AdjustmentReason, Warehouse

        self.freight_expense = Account.objects.create(code="5300", name="Freight", account_type=AccountType.EXPENSE)
        self.freight = ChargeType.objects.create(code="FRT", name="Freight", expense_account=self.freight_expense,
                                                 capitalise_into_inventory=True)
        self.cogs = Account.objects.create(code="5100", name="Cost of sales", account_type=AccountType.EXPENSE)
        self.writeoff_acct = Account.objects.create(code="5800", name="Scrap", account_type=AccountType.EXPENSE)
        self.wip = Account.objects.create(code="1250", name="WIP", account_type=AccountType.ASSET)
        company = Company.get()
        company.default_cogs_account = self.cogs
        company.save()
        self.bay = Warehouse.objects.create(code="BAY", name="Receiving bay")
        self.warehouse.receipt_route = "input"
        self.warehouse.input_warehouse = self.bay
        self.warehouse.save()
        self.reason = AdjustmentReason.objects.create(code="SCRAP", name="Scrapped", account=self.writeoff_acct)
        self.run_made = False

    # -- helpers ---------------------------------------------------------
    def received(self, quantity="10", price="5"):
        self.po = self.order(quantity, price)
        self.receipt = self.receive(self.po, quantity)
        return self.po

    def put_away(self, quantity):
        self.receipt.lines.get().advance(quantity=D(quantity))

    def write_off(self, quantity, day=20):
        from apps.inventory.models import StockAdjustment, StockAdjustmentLine

        adjustment = StockAdjustment.objects.create(adjustment_date=JAN(day), warehouse=self.warehouse,
                                                    reason=self.reason)
        StockAdjustmentLine.objects.create(adjustment=adjustment, item=self.item, uom=self.uom,
                                           quantity=-D(quantity))
        adjustment.post()

    def issue_to_run(self, quantity):
        from apps.inventory.models import Item
        from apps.manufacturing.bom import BillOfMaterials, BomComponent
        from apps.manufacturing.orders import (
            ManufacturingSettings, MaterialIssue, MaterialIssueLine, WorkCentre, WorkOrder,
        )

        if not self.run_made:
            ManufacturingSettings.objects.create(wip_account=self.wip, variance_account=self.cogs)
            sack = Item.objects.create(sku="SACK", name="Sack", uom=self.uom)
            bom = BillOfMaterials.objects.create(item=sack, name="Sack", quantity_produced=D("10"), uom=self.uom)
            BomComponent.objects.create(bom=bom, item=self.item, quantity=D("10"), uom=self.uom, line_number=1)
            self.work_order = WorkOrder.objects.create(
                item=sack, bom=bom, quantity_ordered=D("10"), uom=self.uom, warehouse=self.warehouse,
                work_centre=WorkCentre.objects.create(code="LOOM", name="Loom"))
            self.work_order.release(JAN(15))
            self.run_made = True
        issue = MaterialIssue.objects.create(work_order=self.work_order, issue_date=JAN(15), warehouse=self.warehouse)
        MaterialIssueLine.objects.create(issue=issue, item=self.item, quantity=D(quantity), uom=self.uom,
                                         line_number=1)
        issue.post()
        return issue

    def with_freight(self, amount="80", order=None):
        bill = (order or self.po).create_bill(self.payable, bill_date=JAN(25))
        BillLine.objects.create(bill=bill, charge=self.freight, description="Freight", quantity=D("1"),
                                unit_price=D(amount), expense_account=self.freight_expense)
        bill.post()
        self.bill = bill
        return bill

    def refund(self, bill, quantity="1"):
        line = bill.lines.get(charge=self.freight)
        return bill.create_debit_note(memo="Refund", quantities={line: D(quantity)})

    def books(self):
        return {
            "inventory": self.balance(self.inventory), "wip": self.balance(self.wip),
            "cogs": self.balance(self.cogs), "scrap": self.balance(self.writeoff_acct),
            "freight": self.balance(self.freight_expense), "grni": self.balance(self.grni),
            "ppv": self.balance(self.ppv), "payable": -self.balance(self.payable),
        }

    def assertBooks(self, **expected):
        got = self.books()
        want = {key: D(value) for key, value in expected.items()}
        self.assertEqual({key: got[key] for key in want}, want)

    def assertShelfIsLedger(self):
        self.assertEqual(reconcile_to_ledger()["difference"], D("0.00"))


class EveryFateTests(Fates):
    def fates_then_freight(self):
        self.received()
        self.put_away("6")        # bay 4, WH 6
        self.issue_to_run("2")    # WH 4
        self.write_off("2")       # WH 2
        self.with_freight("80")

    def test_forward_shelf_and_run_get_their_shares(self):
        """Freight 80 over 10 units (8 each): bay 4 -> 32, WH 2 -> 16, run 2 -> 16; inventory 78, WIP 26."""
        self.fates_then_freight()
        self.assertEqual((self.item.stock_value_at(self.bay), self.item.stock_value_at(self.warehouse)),
                         (D("52.00"), D("26.00")))
        self.assertBooks(inventory="78", wip="26", freight="0", grni="0", payable="130")
        self.assertEqual(self.work_order.material_cost(), D("26.00"))
        self.assertShelfIsLedger()

    def test_forward_the_written_off_share_posts_where_the_write_off_posted(self):
        """Integrator's ruling: the 16 on the 2 written off goes to the scrap account (10 + 16), cost of sales 0."""
        self.fates_then_freight()
        self.assertBooks(scrap="26", cogs="0")

    def test_reverse_whole_note_puts_every_account_back(self):
        """Whole freight refunded: inventory 30, WIP 10, scrap 10 (the goods' own), cost of sales 0, payable 50."""
        self.fates_then_freight()
        self.refund(self.bill)
        self.assertBooks(inventory="30", wip="10", scrap="10", cogs="0", freight="0", grni="0", payable="50")
        self.assertEqual((self.item.stock_value_at(self.bay), self.item.stock_value_at(self.warehouse)),
                         (D("20.00"), D("10.00")))
        self.assertEqual(self.work_order.material_cost(), D("10.00"))
        self.assertShelfIsLedger()

    def test_reverse_in_three_notes_each_step_is_its_share(self):
        """Notes of 25, 25, 30 of 80: after note 1 shelf 78-15=63, WIP 26-5=21, write-off share 5 back."""
        self.fates_then_freight()
        self.refund(self.bill, "0.3125")      # 25
        self.assertBooks(inventory="63", wip="21", scrap="21", cogs="0", payable="105")
        self.assertShelfIsLedger()
        self.refund(self.bill, "0.3125")      # 25
        self.assertBooks(inventory="48", wip="16", scrap="16", cogs="0", payable="80")
        self.assertShelfIsLedger()
        self.refund(self.bill, "0.375")       # 30
        self.assertBooks(inventory="30", wip="10", scrap="10", cogs="0", payable="50")
        self.assertShelfIsLedger()

    def test_landing_first_then_fates_then_refund(self):
        """Freight lands on all 10 (130); 6 put away, 2 issued (26), 2 scrapped (26). Refund 80: WIP 10, scrap 10."""
        self.received()
        self.with_freight("80")
        self.put_away("6")
        self.issue_to_run("2")
        self.write_off("2")
        self.assertBooks(inventory="78", wip="26", scrap="26", cogs="0")
        self.refund(self.bill)
        self.assertBooks(inventory="30", wip="10", scrap="10", cogs="0", freight="0", payable="50")
        self.assertEqual(self.work_order.material_cost(), D("10.00"))
        self.assertShelfIsLedger()

    def test_a_return_between_gives_the_return_variance_back(self):
        """Landed 130; 4 sent back (shelf 52, vendor 20, PPV 32); refund 80: PPV 0, inventory 30."""
        self.received()
        self.with_freight("80")
        self.receipt.create_return({self.receipt.lines.get(): D("4")}, debit_bills=False)
        self.assertBooks(ppv="32", inventory="78")
        self.refund(self.bill)
        self.assertBooks(ppv="0", inventory="30", cogs="0")
        self.assertShelfIsLedger()

    def test_rounding_total_first_over_four_fates(self):
        """33.33 over bay 4 / WH 2 / run 2 / scrap 2 of 10: whole refund leaves every account as before the bill."""
        self.received()
        self.put_away("6")
        self.issue_to_run("2")
        self.write_off("2")
        before = self.books()
        self.with_freight("33.33")
        self.refund(self.bill)
        after = self.books()
        for key in ("inventory", "wip", "cogs", "scrap", "freight", "ppv"):
            self.assertEqual(after[key], before[key], key)
        self.assertShelfIsLedger()


class EveryFateFifoTests(EveryFateTests):
    """The same fates and the same figures under FIFO."""

    def setUp(self):
        super().setUp()
        self.item.costing_method = "fifo"
        self.item.save()


class ClosedRunTests(Fates):
    def test_freight_after_the_run_closed_is_variance_and_a_refund_after_that_gives_it_back_from_variance(self):
        """
        6 of 10 issued to a run (WIP 30). Run closed: WIP 0, variance 30. Freight 80 lands: 4 on the shelf (32),
        48 for the run, closed, so variance 78; WIP stays 0. Refund 80: variance 30 again, inventory 20, WIP 0.
        """
        self.received()
        self.put_away("10")
        self.issue_to_run("6")
        self.work_order.close(JAN(20))
        self.assertBooks(wip="0", cogs="30")
        self.with_freight("80")
        self.assertBooks(wip="0", cogs="78", inventory="52")
        self.refund(self.bill)
        self.assertBooks(wip="0", cogs="30", inventory="20")
        self.assertShelfIsLedger()

    def test_freight_while_the_run_is_open_is_cleared_to_variance_by_the_close(self):
        """Freight lands (WIP 78); then the run is closed: WIP 0, variance 78 (nothing was produced)."""
        self.received()
        self.put_away("10")
        self.issue_to_run("6")
        self.with_freight("80")
        self.assertBooks(wip="78")
        self.work_order.close(JAN(20))
        self.assertBooks(wip="0", cogs="78")


class RunReturnsAndUnitsTests(Fates):
    def test_goods_returned_from_the_run_to_the_store_carry_their_freight_back_with_them(self):
        """
        10 @ 5 received to stock, 6 issued to a run (WIP 30), 2 of them returned to the store (WIP 20, shelf 6 = 30).
        Freight 80 lands: the 6 on the shelf bear 48, the 4 left in the run 32. WIP 52, inventory 78.
        """
        from apps.manufacturing.orders import IssueDirection, MaterialIssue, MaterialIssueLine

        self.received()
        self.put_away("10")
        issue = self.issue_to_run("6")
        back = MaterialIssue.objects.create(work_order=self.work_order, issue_date=JAN(16), warehouse=self.warehouse,
                                            direction=IssueDirection.RETURN)
        MaterialIssueLine.objects.create(issue=back, item=self.item, quantity=D("2"), uom=self.uom, line_number=1,
                                         returns_line=issue.lines.get())
        back.post()
        self.assertBooks(wip="20", inventory="30")
        self.with_freight("80")
        self.assertBooks(wip="52", inventory="78", cogs="0")
        self.assertShelfIsLedger()

    def test_an_issue_voided_after_freight_reached_its_run_takes_the_freight_back_with_the_material(self):
        """6 issued (WIP 30), freight 80 lands (WIP 78, inventory 52). The issue voided: material and its freight 48 back."""
        self.received()
        self.put_away("10")
        issue = self.issue_to_run("6")
        self.with_freight("80")
        self.assertBooks(wip="78", inventory="52")
        issue.void()
        self.assertBooks(wip="0", inventory="130")
        self.assertEqual(self.work_order.material_cost(), D("0"))

    def test_an_order_in_cases_stocked_in_eaches(self):
        """
        2 cases @ 60 (12 each): 24 each, 5 each, 120. Received to the bay; 0.5 case (6 each) put away and written off
        there (30). Freight 96 (4 each): the 18 left in the bay bear 72, the 6 written off 24.
        Inventory 120 - 30 + 72 = 162; the written-off 24 in the write-off account (scrap 30 + 24).
        """
        from apps.core.models import UnitOfMeasure

        case = UnitOfMeasure.objects.create(code="case", name="Case", category=self.uom.category,
                                            base_unit=self.uom, conversion_factor=D("12"))
        self.po = self.order("2", "60", uom=case)
        self.receipt = self.receive(self.po, "2")
        self.put_away("0.5")
        self.write_off("6")
        self.with_freight("96")
        self.assertEqual((self.item.stock_value_at(self.bay), self.item.stock_value_at(self.warehouse)),
                         (D("162.00"), D("0.00")))
        self.assertBooks(inventory="162", scrap="54", cogs="0")
        self.assertShelfIsLedger()


class SecondBillTests(Fates):
    def two_receipts_two_bills(self, fifo):
        """
        20 @ 5 ordered. Receipt 1 (10) is entirely written off and bill 1 pays for it. Receipt 2 (10) goes to
        stock, 5 of it written off; bill 2 pays for those 10 and carries freight 60.
        """
        if fifo:
            self.item.costing_method = "fifo"
            self.item.save()
        order = self.order("20", "5")
        line = order.lines.get()
        first = self.receive_on(order, "10", JAN(5))
        first.lines.get().advance()
        self.write_off("10")
        self.hand_bill(line, "10", "5", order=order, day=JAN(10)).post()
        self.receive_on(order, "10", JAN(11)).lines.get().advance()
        self.write_off("5", day=21)
        bill = self.hand_bill(line, "10", "5", order=order, day=JAN(22))
        BillLine.objects.create(bill=bill, charge=self.freight, description="Freight", quantity=D("1"),
                                unit_price=D("60"), expense_account=self.freight_expense)
        bill.post()
        return bill

    def test_fifo_a_second_bills_freight_lands_on_the_goods_that_bill_paid_for(self):
        """
        Bill 2's 60 is for the 10 of receipt 2, 5 of them still on the shelf: 30 on the shelf (5 x 5 + 30 = 55),
        30 on the 5 written off. Spread over both receipts (30 / 30) the first receipt's goods, long gone and
        paid by bill 1, take 30 to cost of sales and the shelf gets 15.
        """
        self.two_receipts_two_bills(fifo=True)
        self.assertEqual(self.item.stock_value_at(self.warehouse), D("55.00"))
        self.assertBooks(inventory="55")

    def test_average_a_second_bills_freight_lands_on_the_goods_that_bill_paid_for(self):
        self.two_receipts_two_bills(fifo=False)
        self.assertEqual(self.item.stock_value_at(self.warehouse), D("55.00"))
        self.assertBooks(inventory="55")


class BilledAheadFreightTests(Fates):
    def test_a_bill_with_freight_posted_before_any_receipt_balances_and_keeps_the_freight(self):
        """Billed as ordered 10 @ 5 with 80 freight, nothing received: payable 130, nowhere to lose 80."""
        order = self.order("10", "5", policy=BillPolicy.ORDERED)
        bill = order.create_bill(self.payable, bill_date=JAN(3))
        BillLine.objects.create(bill=bill, charge=self.freight, description="Freight", quantity=D("1"),
                                unit_price=D("80"), expense_account=self.freight_expense)
        bill.post()
        self.assertBooks(payable="130")
        total_debits = sum(self.books()[k] for k in ("inventory", "grni", "freight", "cogs", "wip"))
        self.assertEqual(total_debits, D("130"))

    def test_billed_ahead_freight_then_receipt_ends_on_the_shelf_or_the_books_say_where(self):
        """Same, then the goods arrive: payable 130; inventory + freight expense + cogs hold all 130."""
        order = self.order("10", "5", policy=BillPolicy.ORDERED)
        bill = order.create_bill(self.payable, bill_date=JAN(3))
        BillLine.objects.create(bill=bill, charge=self.freight, description="Freight", quantity=D("1"),
                                unit_price=D("80"), expense_account=self.freight_expense)
        bill.post()
        self.receive(order, "10")
        books = self.books()
        self.assertEqual(books["payable"], D("130"))
        self.assertEqual(books["grni"], D("0"))
        self.assertEqual(books["inventory"] + books["freight"] + books["cogs"], D("130"))
        self.assertShelfIsLedger()


class StandardCostTests(Fates):
    def test_standard_cost_item_whole_refund_leaves_shelf_and_ledger_where_they_started(self):
        """A standard-cost item (5): bill's own freight 80 then refunded in full. Ledger inventory and shelf agree."""
        self.item.costing_method = "standard"
        self.item.standard_cost = D("5")
        self.item.save()
        self.received()
        self.with_freight("80")
        mid = (self.balance(self.inventory), self.item.stock_value_at(self.bay))
        self.refund(self.bill)
        self.assertShelfIsLedger()
        self.assertEqual(self.balance(self.inventory), self.item.stock_value_at(self.bay),
                         f"after landing {mid}")


class GrniRandomTests(base.ProbeCase):
    """
    GRNI must net to zero to the paisa once everything received (net of returns) is billed, whatever the order of
    receipts, bills (some billed ahead), returns between them and price changes. Seeds are fixed; a failing seed is
    reproduced by hand in the report.
    """

    def run_seed(self, seed, ahead, returns, price_change):
        import random

        from django.core.exceptions import ValidationError

        from apps.core.models import Company

        rnd = random.Random(seed)
        company = Company.get()
        company.purchase_price_tolerance_percent = D("60")
        company.save()
        price = rnd.choice(["3.3333", "10.10", "7.777", "0.3333", "12.3457"])
        discount = rnd.choice(["0", "2.5", "12.5", "7.7"])
        quantity = rnd.choice(["7", "10", "13.5", "9.25"])
        policy = BillPolicy.ORDERED if ahead else BillPolicy.RECEIVED
        order = self.order(quantity, price, discount=discount, policy=policy)
        line = order.lines.get()
        todo = rnd.sample(["receive", "bill", "receive", "bill", "bill", "receive"] + (["return"] if returns else []), 6)
        todo += ["receive", "bill", "bill"]
        receipts = []
        day = 2
        log = []
        for what in todo:
            day += 1
            try:
                if what == "receive":
                    left = line.quantity - sum((r.lines.get().quantity_received for r in receipts), D("0"))
                    if left <= 0:
                        continue
                    part = (left if what == todo[-3] else (left * D(rnd.choice(["0.25", "0.5", "0.4"]))).quantize(D("0.01")))
                    if part <= 0:
                        continue
                    receipts.append(self.receive_on(order, str(part), JAN(day)))
                    log.append(("receive", part))
                elif what == "bill":
                    room = line.bill_limit() - line.quantity_billed()
                    if policy == BillPolicy.RECEIVED:
                        room = min(room, line.quantity_received() - line.quantity_billed())
                    if room <= 0:
                        continue
                    part = (room * D(rnd.choice(["0.3", "0.5", "1"]))).quantize(D("0.01"))
                    if part <= 0:
                        continue
                    bill_price = D(price)
                    if price_change and rnd.random() < 0.5:
                        bill_price = (bill_price * D("1.1")).quantize(D("0.0001"))
                    self.hand_bill(line, str(part), str(bill_price), discount=discount, order=order,
                                   day=JAN(day)).post()
                    log.append(("bill", part, bill_price))
                elif what == "return" and receipts:
                    victim = rnd.choice(receipts)
                    row = victim.lines.get()
                    back = (row.quantity_received * D(rnd.choice(["0.2", "0.3"]))).quantize(D("0.01"))
                    if back > 0:
                        victim.create_return({row: back})
                        log.append(("return", back))
            except ValidationError as refused:
                log.append(("refused", what, str(refused)[:60]))
        # Settle: bill whatever is received and not billed.
        left = max(line.quantity_received() - line.quantity_billed(), D("0"))
        if left > 0:
            self.hand_bill(line, str(left), price, discount=discount, order=order, day=JAN(28)).post()
            log.append(("final bill", left))
        return log

    def check(self, seed, **kw):
        log = self.run_seed(seed, **kw)
        grni = self.balance(self.grni)
        import os
        if os.environ.get("GRNI_DEBUG") and grni:
            from apps.accounting.models import JournalLine
            for row in JournalLine.objects.filter(account=self.grni, entry__posted=True).order_by("entry__date", "id"):
                print("   GRNI", row.entry.date, row.entry.reference or row.entry.memo[:30], row.debit, row.credit)
        gap = reconcile_to_ledger()["difference"]
        self.assertEqual((grni, gap), (D("0.00"), D("0.00")), f"(GRNI, shelf-ledger gap) seed {seed}: {log}")


def _seeded(name, **kw):
    attrs = {f"test_seed_{seed:02d}": (lambda self, seed=seed: self.check(seed, **kw)) for seed in range(30)}
    globals()[name] = type(name, (GrniRandomTests,), attrs)


_seeded("GrniInParts", ahead=False, returns=False, price_change=False)
_seeded("GrniPriceChanges", ahead=False, returns=False, price_change=True)
_seeded("GrniBilledAhead", ahead=True, returns=False, price_change=True)
_seeded("GrniWithReturns", ahead=False, returns=True, price_change=True)
del GrniRandomTests


class GrniPaisaTests(base.ProbeCase):
    def test_billed_ahead_in_three_parts_clears_to_the_paisa(self):
        """
        O89 in parts, the O93 numbers: 3 @ 10.10 less 2.5% accrues 3 x 9.8475 = 29.5425 -> 29.54 when received.
        Billed ahead 1 + 1 + 1, each bill clears round(9.8475) = 9.85 on its own: 29.55. GRNI +0.01.
        """
        order = self.order("3", "10.10", discount="2.5", policy=BillPolicy.ORDERED)
        line = order.lines.get()
        for _ in range(3):
            self.hand_bill(line, "1", "10.10", discount="2.5", order=order).post()
        self.receive(order, "3")
        self.assertEqual(self.balance(self.grni), D("0.00"))

    def test_a_return_between_clears_to_the_paisa(self):
        """
        7 @ 10.10 less 2.5% (9.8475) accrues round(68.9325) = 68.93. 1.4 sent back unbilled debits round(13.7865) =
        13.79; the 5.6 left billed 2.8 + 1.4 + 1.4 clear round(55.146) = 55.15 total first. 13.79 + 55.15 = 68.94: GRNI +0.01.
        """
        order = self.order("7", "10.10", discount="2.5")
        receipt = self.receive(order, "7")
        receipt.create_return({receipt.lines.get(): D("1.4")})
        line = order.lines.get()
        for quantity in ("2.8", "1.4", "1.4"):
            self.hand_bill(line, quantity, "10.10", discount="2.5", order=order).post()
        self.assertEqual(self.balance(self.grni), D("0.00"))


class ReturnsBillsProbes(base.ProbeCase):
    def test_returns_from_two_receipts_debit_the_bill_that_paid_for_each(self):
        """
        20 @ 5 (tolerance 10%). Receipt 1 (10) billed 5.00; receipt 2 (10) billed 5.40 (variance 4.00).
        Back: 4 of receipt 1 (20.00), then 4 of receipt 2 (21.60), then 3 more of receipt 2 (16.20).
        Debited 57.80 in all, variance kept 4.00 - 0.40 x 7 = 1.20, GRNI 0.
        """
        company = Company.get()
        company.purchase_price_tolerance_percent = D("10")
        company.save()
        order = self.order("20", "5")
        line = order.lines.get()
        first = self.receive_on(order, "10", JAN(5))
        self.hand_bill(line, "10", "5.00", order=order, day=JAN(10)).post()
        second = self.receive_on(order, "10", JAN(11))
        self.hand_bill(line, "10", "5.40", order=order, day=JAN(12)).post()
        debited = D("0")
        for receipt, quantity in ((first, "4"), (second, "4"), (second, "3")):
            returned = receipt.create_return({receipt.lines.get(): D(quantity)})
            debited += sum((note.total() for note in returned.debit_notes_created), D("0"))
        self.assertEqual((debited, self.balance(self.ppv), self.balance(self.grni)),
                         (D("57.80"), D("1.20"), D("0")), "(debited, PPV, GRNI)")
        self.assertShelfIsLedger = lambda: self.assertEqual(reconcile_to_ledger()["difference"], D("0.00"))
        self.assertShelfIsLedger()

    def test_a_fractional_return_between_two_bills(self):
        """10 @ 5 received; 2.5 sent back before billing, then 7.5 billed: debit nothing, GRNI 0, payable 37.50."""
        order = self.order("10", "5")
        receipt = self.receive(order, "10")
        returned = receipt.create_return({receipt.lines.get(): D("2.5")})
        self.assertEqual(list(returned.debit_notes_created), [])
        self.hand_bill(order.lines.get(), "7.5", "5", order=order, day=JAN(12)).post()
        self.assertEqual((self.balance(self.grni), -self.balance(self.payable), self.balance(self.inventory)),
                         (D("0"), D("37.50"), D("37.50")))


class CapitaliseProbes(base.ProbeCase):
    def category(self):
        from apps.assets.models import AssetCategory

        acc = lambda code, name, kind: Account.objects.create(code=code, name=name, account_type=kind)  # noqa: E731
        self.plant = acc("1500", "Plant", AccountType.ASSET)
        return AssetCategory.objects.create(
            code="PLANT", name="Plant", asset_account=self.plant,
            accumulated_account=acc("1590", "Accumulated", AccountType.ASSET),
            expense_account=acc("6100", "Depreciation", AccountType.EXPENSE),
            disposal_account=acc("6200", "Disposal", AccountType.EXPENSE), default_life_months=60)

    def test_three_machines_billed_dearer_than_received_variance_comes_out_of_ppv_and_the_undo_restores_it(self):
        """
        3 @ 4,000 received (inventory 12,000, GRNI -12,000); billed at 4,100 (12,300; PPV +300, tolerance 10%).
        Capitalised: plant 12,300, inventory 0, PPV 0 (the bill's 300 comes back credited), GRNI 0.
        Un-capitalised: plant 0, inventory 12,000 (shelf too), PPV +300 again.
        """
        company = Company.get()
        company.purchase_price_tolerance_percent = D("10")
        company.save()
        category = self.category()
        order = self.order("3", "4000")
        self.receive(order, "3")
        bill = self.hand_bill(order.lines.get(), "3", "4100", order=order, day=JAN(10))
        bill.post()
        assets = bill.lines.get().capitalise_as_asset(category)
        self.assertEqual(len(assets), 3)
        self.assertEqual((self.balance(self.plant), self.balance(self.inventory), self.balance(self.ppv),
                          self.balance(self.grni)), (D("12300.00"), D("0"), D("0"), D("0")))
        self.assertEqual(self.item.on_hand_at(self.warehouse), D("0"))
        for asset in assets:
            asset.uncapitalise()
        self.assertEqual((self.balance(self.plant), self.balance(self.inventory), self.balance(self.ppv)),
                         (D("0"), D("12000.00"), D("300")))
        self.assertEqual(self.item.stock_value_at(self.warehouse), D("12000.00"))
        self.assertEqual(reconcile_to_ledger()["difference"], D("0.00"))

    def test_a_machine_still_in_the_bay_is_taken_from_the_bay(self):
        from apps.inventory.models import Warehouse

        category = self.category()
        bay = Warehouse.objects.create(code="BAY", name="Bay")
        self.warehouse.receipt_route = "input"
        self.warehouse.input_warehouse = bay
        self.warehouse.save()
        order = self.order("1", "12000")
        self.receive(order, "1")
        bill = order.create_bill(self.payable, bill_date=JAN(10))
        bill.post()
        bill.lines.get().capitalise_as_asset(category)
        self.assertEqual((self.item.on_hand_at(bay), self.item.on_hand_at(self.warehouse),
                          self.balance(self.inventory)), (D("0"), D("0"), D("0")))
        self.assertEqual(reconcile_to_ledger()["difference"], D("0.00"))

    def test_a_machine_in_foreign_currency_leaves_inventory_at_the_receipt_rate(self):
        """EUR 1,000 machine received at 90 (90,000), billed at 95 (95,000): plant 95,000; inventory 0; GRNI 0; FX 5,000."""
        from apps.core.models import Currency, ExchangeRate
        from .models import PurchaseOrder, PurchaseOrderLine

        category = self.category()
        eur = Currency.objects.create(code="EUR", name="Euro")
        for day, rate in ((1, "90"), (10, "95")):
            ExchangeRate.objects.create(currency=eur, rate=D(rate), valid_from=JAN(day))
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN(1), currency=eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("1"),
                                         unit_price=D("1000"))
        order.confirm()
        self.receive_on(order, "1", JAN(5))
        bill = self.hand_bill(order.lines.get(), "1", "1000", order=order, currency=eur, day=JAN(10))
        bill.post()
        bill.lines.get().capitalise_as_asset(category)
        self.assertEqual((self.balance(self.plant), self.balance(self.inventory), self.balance(self.grni)),
                         (D("95000.00"), D("0"), D("0")))
        self.assertEqual(reconcile_to_ledger()["difference"], D("0.00"))


from .tests_subcontract import SubcontractTestCase  # noqa: E402


class SubcontractReturnProbes(SubcontractTestCase):
    """O206/O38 at every step, not only when everything is back: |ledger move - shelf move| <= 0.005 each time."""

    def lots(self):
        import datetime as dt

        from apps.inventory.models import Item, Lot, MovementType, StockMovement
        from django.utils import timezone

        Item.objects.filter(pk=self.frame.pk).update(tracking="lot")
        self.frame.refresh_from_db()
        StockMovement.objects.filter(item=self.frame).delete()
        for code, quantity, expires in (("F-1", "1.11", dt.date(2026, 12, 31)), ("F-2", "98.89", dt.date(2027, 6, 30))):
            made = Lot.objects.create(item=self.frame, code=code, expires_on=expires)
            StockMovement.objects.create(item=self.frame, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
                                         uom=self.frame.uom, quantity=D(quantity), unit_cost=D("8"), lot=made,
                                         occurred_at=timezone.now())

    def total_value(self):
        return sum((item.stock_value_at(place) for item in (self.frame, self.motor, self.assembly)
                    for place in (self.warehouse, self.subcontractor)), D("0"))

    def run_returns(self, steps, per):
        from apps.accounting.models import Account, AccountType, JournalLine
        from django.db.models import Sum

        from .models import SubcontractComponent

        self.lots()
        company = Company.get()
        ppv = Account.objects.create(code="5900", name="PPV", account_type=AccountType.EXPENSE)
        company.purchase_price_variance_account = ppv
        company.save()
        order = self.subcontract_order("10", confirm=False)
        SubcontractComponent.objects.filter(item=self.frame).update(quantity_per=D(per))
        order.confirm()
        order.issue_components(self.warehouse)

        def ledger():
            rows = JournalLine.objects.filter(account=self.inventory, entry__posted=True).aggregate(
                d=Sum("debit"), c=Sum("credit"))
            return (rows["d"] or D("0")) - (rows["c"] or D("0"))

        ledger0, shelf0 = ledger(), self.total_value()
        receipt = self.receive(order, "10")
        line = receipt.lines.get()
        gaps = []
        for step in steps:
            receipt.create_return(quantities={line: D(step)}, debit_bills=False)
            gaps.append(((ledger() - ledger0) - (self.total_value() - shelf0)))
        return gaps

    def test_every_partial_step_stays_within_half_a_paisa(self):
        gaps = self.run_returns((1, 1, 3), "0.333")
        # after the receipt itself the books and the shelf both include it; only the returns move the gap
        self.assertTrue(all(abs(gap - gaps[0] + gaps[0]) <= D("0.005") for gap in gaps), gaps)

    def test_a_different_fraction_and_order(self):
        gaps = self.run_returns((2, 2, 2, 2, 2), "0.1237")
        self.assertTrue(all(abs(gap) <= D("0.005") for gap in gaps), gaps)


class GrniForeignAheadTests(base.ProbeCase):
    def test_a_euro_bill_ahead_of_its_goods_clears_grni_at_the_rate_the_goods_came_in_at(self):
        """
        EUR 10 @ 5 billed as ordered on 3 Jan at 95 (Dr GRNI 4,750), received on 5 Jan at 92 (Cr GRNI 4,600).
        GRNI must be 0 with the 150 in exchange; the bill froze 4,750 and the receipt credits 4,600.
        """
        from apps.core.models import Currency, ExchangeRate
        from .models import PurchaseOrder, PurchaseOrderLine

        eur = Currency.objects.create(code="EUR", name="Euro")
        for day, rate in ((1, "95"), (4, "92")):
            ExchangeRate.objects.create(currency=eur, rate=D(rate), valid_from=JAN(day))
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN(1), currency=eur,
                                             bill_policy=BillPolicy.ORDERED)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                         unit_price=D("5"))
        order.confirm()
        self.hand_bill(order.lines.get(), "10", "5", order=order, currency=eur, day=JAN(3)).post()
        self.receive_on(order, "10", JAN(5))
        self.assertEqual(self.balance(self.grni), D("0.00"))
