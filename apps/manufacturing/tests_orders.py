"""
A run, end to end, with the money checked.

The scenario is one extrusion order: a thousand kilos of 1,000-denier
tape from a blend of virgin polymer, regrind, filler and masterbatch.
Every figure asserted was worked out from the specification and the
shelf, and the arithmetic is in the comment beside it.

The test that matters most is the last one in each class: after a run
is closed, the work-in-progress account holds nothing. Everything that
went in has come out as stock, as scrap or as variance, and a
work-in-progress balance that survives a closed order is material this
system has lost track of.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, tag
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import (
    Company,
    Currency,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)
from apps.inventory.models import Item, Lot, MovementType, StockMovement, Warehouse
from apps.inventory.tracking import TrackingMode

from .bom import BillOfMaterials, BomByproduct, BomComponent, ByproductValuation
from .orders import (
    IssueDirection,
    ManufacturingSettings,
    MaterialIssue,
    MaterialIssueLine,
    ProductionByproduct,
    ProductionEntry,
    WorkCentre,
    WorkOrder,
    WorkOrderStatus,
)

TODAY = datetime.date(2026, 6, 1)


class RunTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.kg = UnitOfMeasure.objects.create(
            code="kg", name="Kilogram", category=UnitOfMeasureCategory.WEIGHT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.wip = acc("1250", "Work in progress", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.variance = acc("5100", "Production variance", AccountType.EXPENSE)
        self.scrap = acc("5200", "Production scrap", AccountType.EXPENSE)
        self.opening = acc("3900", "Opening balances", AccountType.EQUITY)
        Company.objects.create(
            name="Sack Co", base_currency=self.usd,
            default_inventory_account=self.inventory,
            default_cogs_account=self.cogs,
        )
        ManufacturingSettings.objects.create(
            wip_account=self.wip, variance_account=self.variance,
            scrap_account=self.scrap,
        )
        self.plant = Warehouse.objects.create(code="P", name="Plant")
        self.loom = WorkCentre.objects.create(code="EXT-1", name="Extrusion line 1")

        make = lambda sku, name, standard=None: Item.objects.create(
            sku=sku, name=name, uom=self.kg, standard_cost=standard
        )
        self.virgin = make("PP-RAFFIA", "PP homopolymer")
        self.regrind = make("REGRIND", "Reprocessed waste", Decimal("60"))
        self.filler = make("CACO3", "Calcium carbonate")
        self.colour = make("MB-WHITE", "White masterbatch")
        self.tape = make("TAPE-1000", "PP tape, 1000 denier")

        for item, quantity, cost in (
            (self.virgin, "2000", "100"),
            (self.regrind, "500", "60"),
            (self.filler, "300", "30"),
            (self.colour, "100", "200"),
        ):
            self.stock(item, quantity, cost)

        self.bom = BillOfMaterials.objects.create(
            item=self.tape, name="Tape 1000 den",
            quantity_produced=Decimal("100"), uom=self.kg,
        )
        for index, (item, net) in enumerate((
            (self.virgin, "75"), (self.regrind, "15"),
            (self.filler, "8"), (self.colour, "2"),
        ), start=1):
            BomComponent.objects.create(
                bom=self.bom, item=item, quantity=Decimal(net), uom=self.kg,
                waste_percent=Decimal("3"), line_number=index,
            )
        BomByproduct.objects.create(
            bom=self.bom, item=self.regrind,
            quantity=Decimal("2.474227"), uom=self.kg,
            valuation=ByproductValuation.STANDARD,
        )

    def stock(self, item, quantity, cost, lot=None):
        """Put stock on the shelf without going through a document."""
        return StockMovement.objects.create(
            item=item, warehouse=self.plant, movement_type=MovementType.RECEIPT,
            uom=self.kg, quantity=Decimal(quantity), unit_cost=Decimal(cost),
            lot=lot, occurred_at=timezone.now(),
        )

    def issue_with_lots(self, order, rows):
        """An issue whose lines name the batch they came out of."""
        document = MaterialIssue.objects.create(
            work_order=order, issue_date=TODAY, warehouse=self.plant,
        )
        for index, (item, quantity, lot) in enumerate(rows, start=1):
            MaterialIssueLine.objects.create(
                issue=document, item=item, quantity=Decimal(quantity),
                uom=self.kg, lot=lot, line_number=index,
            )
        return document

    def order(self, quantity="1000"):
        return WorkOrder.objects.create(
            item=self.tape, bom=self.bom, quantity_ordered=Decimal(quantity),
            uom=self.kg, warehouse=self.plant, work_centre=self.loom,
        )

    def issue(self, order, rows, direction=IssueDirection.ISSUE):
        document = MaterialIssue.objects.create(
            work_order=order, direction=direction, issue_date=TODAY,
            warehouse=self.plant,
        )
        for index, row in enumerate(rows, start=1):
            item, quantity = row[0], row[1]
            MaterialIssueLine.objects.create(
                issue=document, item=item, quantity=Decimal(quantity),
                uom=self.kg, line_number=index,
                returns_line=row[2] if len(row) > 2 else None,
            )
        return document

    def produce(self, order, quantity, scrapped="0", byproducts=(), lot=None,
                uom=None):
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal(quantity),
            quantity_scrapped=Decimal(scrapped), uom=uom or self.kg,
            work_centre=self.loom, lot=lot,
        )
        for index, (item, amount) in enumerate(byproducts, start=1):
            ProductionByproduct.objects.create(
                entry=entry, item=item, quantity=Decimal(amount), uom=self.kg,
                line_number=index,
            )
        return entry

    def balance(self, account):
        """Debits less credits on a posted account."""
        rows = JournalLine.objects.filter(account=account, entry__posted=True)
        totals = rows.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        return (totals["debit"] or Decimal("0")) - (totals["credit"] or Decimal("0"))

    def full_issue(self, order):
        """Exactly what the order says it needs."""
        return self.issue(order, [
            (component.item, component.quantity_required)
            for component in order.components.all()
        ])


class ReleasingFreezesTheArithmeticTests(RunTestCase):
    def test_the_requirements_are_copied_off_the_bom(self):
        order = self.order()
        order.release(TODAY)
        wanted = {
            c.item.sku: c.quantity_required for c in order.components.all()
        }
        # 75 kg of virgin in a 100 kg batch, 3% of the input lost, ten
        # batches: 75 / 0.97 x 10 = 773.195876 kg.
        self.assertAlmostEqual(
            wanted["PP-RAFFIA"], Decimal("773.195876"), places=5
        )
        self.assertAlmostEqual(wanted["REGRIND"], Decimal("154.639175"), places=5)
        self.assertAlmostEqual(wanted["CACO3"], Decimal("82.474227"), places=5)
        self.assertAlmostEqual(wanted["MB-WHITE"], Decimal("20.618557"), places=5)

    def test_the_planned_cost_is_worked_out_and_frozen(self):
        order = self.order()
        order.release(TODAY)
        # 773.195876 x 100 + 154.639175 x 60 + 82.474227 x 30
        #   + 20.618557 x 200 = 93,195.876289, less 24.742268 kg of
        # regrind back at its standard 60 = 1,484.536082.
        self.assertAlmostEqual(
            order.planned_unit_cost, Decimal("91.711340"), places=5
        )

    def test_a_later_change_to_the_bom_does_not_move_a_released_order(self):
        order = self.order()
        order.release(TODAY)
        frozen = order.planned_unit_cost
        line = self.bom.components.get(item=self.virgin)
        line.quantity = Decimal("85")
        line.save()
        order.refresh_from_db()
        self.assertEqual(order.planned_unit_cost, frozen)
        self.assertAlmostEqual(
            order.components.get(item=self.virgin).quantity_required,
            Decimal("773.195876"), places=5,
        )

    def test_a_bom_that_makes_something_else(self):
        other = BillOfMaterials.objects.create(
            item=self.virgin, quantity_produced=Decimal("100"), uom=self.kg
        )
        BomComponent.objects.create(
            bom=other, item=self.filler, quantity=Decimal("1"), uom=self.kg
        )
        order = self.order()
        order.bom = other
        order.save()
        with self.assertRaises(ValidationError) as caught:
            order.release(TODAY)
        self.assertIn("makes", str(caught.exception))

    def test_a_byproduct_nobody_can_value(self):
        # Asked at release, when a loom has not yet run for three days.
        self.regrind.standard_cost = None
        self.regrind.save()
        order = self.order()
        with self.assertRaises(ValidationError) as caught:
            order.release(TODAY)
        self.assertIn("no standard cost", str(caught.exception))

    def test_a_bom_with_no_components(self):
        empty = BillOfMaterials.objects.create(
            item=self.tape, version=2, is_default=False,
            quantity_produced=Decimal("100"), uom=self.kg,
        )
        order = self.order()
        order.bom = empty
        order.save()
        with self.assertRaises(ValidationError):
            order.release(TODAY)

    def test_releasing_twice(self):
        order = self.order()
        order.release(TODAY)
        with self.assertRaises(ValidationError):
            order.release(TODAY)


class MaterialGoesIntoWorkInProgressTests(RunTestCase):
    def test_an_issue_moves_stock_and_posts_the_ledger(self):
        order = self.order()
        order.release(TODAY)
        document = self.issue(order, [(self.virgin, "773.1959")])
        document.post()
        # The shelf carried 2,000 kg at a flat 100, so this costs
        # 77,319.59 and inventory falls by it.
        self.assertAlmostEqual(
            document.posted_value, Decimal("77319.59"), places=2
        )
        self.assertAlmostEqual(self.balance(self.wip), Decimal("77319.59"), places=2)
        self.assertEqual(
            self.virgin.on_hand_at(self.plant),
            Decimal("2000") - Decimal("773.1959"),
        )

    def test_the_run_holds_what_went_in(self):
        order = self.order()
        order.release(TODAY)
        self.full_issue(order).post()
        # The whole blend at the shelf's prices. An issue line is
        # recorded to four places, not the six the requirement carries,
        # because a store weighs to the gramme and not past it:
        # 773.1959 x 100 + 154.6392 x 60 + 82.4742 x 30 + 20.6186 x 200.
        self.assertAlmostEqual(
            order.material_cost(), Decimal("93195.888"), places=2
        )
        self.assertAlmostEqual(
            order.wip_balance(), Decimal("93195.888"), places=2
        )

    def test_material_cannot_be_drawn_against_a_closed_order(self):
        order = self.order()
        order.release(TODAY)
        order.close(TODAY)
        document = self.issue(order, [(self.virgin, "10")])
        with self.assertRaises(ValidationError) as caught:
            document.post()
        self.assertIn("released order", str(caught.exception))

    def test_a_posted_issue_cannot_be_edited(self):
        order = self.order()
        order.release(TODAY)
        document = self.issue(order, [(self.virgin, "10")])
        document.post()
        document.memo = "second thoughts"
        with self.assertRaises(ValidationError):
            document.save()


class OutputComesBackOutTests(RunTestCase):
    def setUp(self):
        super().setUp()
        self.run = self.order()
        self.run.release(TODAY)
        self.full_issue(self.run).post()

    def test_production_lands_at_the_planned_cost(self):
        entry = self.produce(self.run, "1000")
        entry.post()
        self.assertEqual(entry.unit_cost, self.run.planned_unit_cost)
        self.assertEqual(self.tape.on_hand_at(self.plant), Decimal("1000"))
        # 1,000 kg at 91.711340 = 91,711.34 out of work in progress.
        self.assertAlmostEqual(
            entry.posted_value, Decimal("91711.34"), places=2
        )

    def test_a_byproduct_comes_back_at_what_the_bom_says_it_is_worth(self):
        before = self.regrind.on_hand_at(self.plant)
        entry = self.produce(
            self.run, "1000", byproducts=[(self.regrind, "24.7423")]
        )
        entry.post()
        row = entry.byproducts.get()
        self.assertEqual(row.unit_value, Decimal("60"))
        self.assertEqual(
            self.regrind.on_hand_at(self.plant), before + Decimal("24.7423")
        )

    def test_a_byproduct_the_bom_never_mentioned(self):
        entry = self.produce(self.run, "900", byproducts=[(self.filler, "10")])
        with self.assertRaises(ValidationError) as caught:
            entry.post()
        self.assertIn("appearing from nowhere", str(caught.exception))

    def test_scrap_is_written_off_rather_than_shelved(self):
        entry = self.produce(self.run, "900", scrapped="100")
        entry.post()
        self.assertEqual(self.tape.on_hand_at(self.plant), Decimal("900"))
        # 100 kg at the planned 91.711340 = 9,171.13.
        self.assertAlmostEqual(self.balance(self.scrap), Decimal("9171.13"), places=2)

    def test_output_cannot_be_booked_against_an_unreleased_order(self):
        draft = self.order("50")
        entry = self.produce(draft, "50")
        with self.assertRaises(ValidationError):
            entry.post()


class ClosingLeavesWorkInProgressEmptyTests(RunTestCase):
    """
    The one that matters. Everything that went into a run has to come
    out of it as stock, as scrap or as variance; a balance surviving a
    closed order is material the system has lost.
    """

    def test_a_run_that_went_exactly_to_plan(self):
        order = self.order()
        order.release(TODAY)
        self.full_issue(order).post()
        self.produce(
            order, "1000", byproducts=[(self.regrind, "24.742268")]
        ).post()
        order.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        # Plan and actual agreed, so there is nothing to explain.
        self.assertLess(abs(self.balance(self.variance)), Decimal("0.02"))

    def test_a_run_that_ate_more_polymer_than_it_should_have(self):
        order = self.order()
        order.release(TODAY)
        rows = [
            (component.item, component.quantity_required)
            for component in order.components.all()
        ]
        # Fifty kilos of virgin over the specification, at 100 a kilo.
        rows[0] = (self.virgin, rows[0][1] + Decimal("50"))
        self.issue(order, rows).post()
        self.produce(
            order, "1000", byproducts=[(self.regrind, "24.742268")]
        ).post()
        order.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertAlmostEqual(self.balance(self.variance), Decimal("5000"), places=0)

    def test_a_run_that_produced_nothing_at_all(self):
        order = self.order()
        order.release(TODAY)
        self.full_issue(order).post()
        order.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertAlmostEqual(
            self.balance(self.variance), Decimal("93195.89"), places=1
        )

    def test_the_variance_in_kilos_says_which_material_moved(self):
        # Read before the money one, because it names the problem.
        order = self.order()
        order.release(TODAY)
        rows = [
            (component.item, component.quantity_required)
            for component in order.components.all()
        ]
        rows[0] = (self.virgin, rows[0][1] + Decimal("50"))
        self.issue(order, rows).post()
        variance = {
            item.sku: (want, got, diff)
            for item, want, got, diff in order.material_variance()
        }
        self.assertAlmostEqual(variance["PP-RAFFIA"][2], Decimal("50"), places=4)
        self.assertAlmostEqual(variance["CACO3"][2], Decimal("0"), places=4)

    def test_closing_an_order_that_is_not_released(self):
        order = self.order()
        with self.assertRaises(ValidationError):
            order.close(TODAY)


class EveryForwardPathHasItsReverseTests(RunTestCase):
    def setUp(self):
        super().setUp()
        self.run = self.order()
        self.run.release(TODAY)

    def test_voiding_an_issue_puts_the_material_back(self):
        document = self.issue(self.run, [(self.virgin, "100")])
        document.post()
        document.void(TODAY)
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("2000"))
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertEqual(self.run.material_cost(), Decimal("0"))

    def test_returning_unused_material_credits_the_run_at_what_it_cost(self):
        document = self.issue(self.run, [(self.virgin, "100")])
        document.post()
        line = document.lines.get()
        # The shelf has moved since: more polymer arrived at a different
        # price. The return must still come back at 100.
        self.stock(self.virgin, "1000", "140")
        back = self.issue(
            self.run, [(self.virgin, "40", line)], direction=IssueDirection.RETURN
        )
        back.post()
        self.assertEqual(back.lines.get().unit_cost, Decimal("100"))
        self.assertAlmostEqual(
            self.run.material_cost(), Decimal("6000"), places=2
        )
        self.assertAlmostEqual(self.balance(self.wip), Decimal("6000"), places=2)

    def test_a_return_that_names_no_issue(self):
        back = self.issue(
            self.run, [(self.virgin, "10")], direction=IssueDirection.RETURN
        )
        with self.assertRaises(ValidationError) as caught:
            back.post()
        self.assertIn("must name the issue line", str(caught.exception))

    def test_a_return_against_another_order(self):
        other = self.order("100")
        other.release(TODAY)
        document = self.issue(other, [(self.virgin, "50")])
        document.post()
        back = self.issue(
            self.run, [(self.virgin, "10", document.lines.get())],
            direction=IssueDirection.RETURN,
        )
        with self.assertRaises(ValidationError):
            back.post()

    def test_voiding_an_issue_that_has_already_been_returned_against(self):
        document = self.issue(self.run, [(self.virgin, "100")])
        document.post()
        back = self.issue(
            self.run, [(self.virgin, "40", document.lines.get())],
            direction=IssueDirection.RETURN,
        )
        back.post()
        with self.assertRaises(ValidationError) as caught:
            document.void(TODAY)
        self.assertIn("Void the return first", str(caught.exception))

    def test_voiding_production_takes_the_output_back_off_the_shelf(self):
        self.full_issue(self.run).post()
        entry = self.produce(
            self.run, "1000", byproducts=[(self.regrind, "24.742268")]
        )
        entry.post()
        before = self.regrind.on_hand_at(self.plant)
        entry.void(TODAY)
        self.assertEqual(self.tape.on_hand_at(self.plant), Decimal("0"))
        self.assertEqual(
            self.regrind.on_hand_at(self.plant),
            before - Decimal("24.7423"),
        )
        self.assertAlmostEqual(
            self.run.wip_balance(), Decimal("93195.888"), places=2
        )

    def test_cancelling_an_order_nothing_has_touched(self):
        self.run.cancel()
        self.assertEqual(self.run.status, WorkOrderStatus.CANCELLED)

    def test_an_order_with_polymer_in_the_hopper_cannot_be_cancelled(self):
        self.issue(self.run, [(self.virgin, "100")]).post()
        with self.assertRaises(ValidationError) as caught:
            self.run.cancel()
        self.assertIn("close it", str(caught.exception).lower())

    def test_reopening_reverses_the_close_rather_than_editing_it(self):
        self.full_issue(self.run).post()
        self.run.close(TODAY)
        closing = self.run.close_entry
        self.assertIsNotNone(closing)
        self.run.reopen(TODAY)
        self.assertEqual(self.run.status, WorkOrderStatus.RELEASED)
        self.assertIsNotNone(self.run.reopened_entry)
        # The close and its reversal cancel, so the run is holding its
        # material again and the variance account is clean.
        self.assertEqual(self.balance(self.variance), Decimal("0"))
        self.assertAlmostEqual(
            self.balance(self.wip), Decimal("93195.89"), places=1
        )

    def test_and_then_it_can_be_closed_again(self):
        self.full_issue(self.run).post()
        self.run.close(TODAY)
        self.run.reopen(TODAY)
        self.produce(
            self.run, "1000", byproducts=[(self.regrind, "24.742268")]
        ).post()
        self.run.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_a_closed_order_cannot_be_edited(self):
        self.run.close(TODAY)
        self.run.notes = "after the fact"
        with self.assertRaises(ValidationError):
            self.run.save()


class AReturnHandsBackWhatItsLineDrewTests(RunTestCase):
    """
    Found by probing: a return was checked only for naming a line on the
    same run. Issued 100 kg and returned 150, the shelf held 2,050 of the
    2,000 it started with and work in progress -5,000. Two full returns
    of one line, or one against a voided issue, made 2,100 and -10,000;
    filler came back at masterbatch's 200 a kilo against a masterbatch
    line. The mirror of a void, which refuses while returns stand.
    """

    def setUp(self):
        super().setUp()
        self.job = self.order()
        self.job.release(TODAY)

    def drawn(self, item=None, quantity="100"):
        document = self.issue(self.job, [(item or self.virgin, quantity)])
        document.post()
        return document.lines.get()

    def back(self, line, quantity, item=None):
        return self.issue(self.job, [(item or self.virgin, quantity, line)],
                          direction=IssueDirection.RETURN)

    def test_no_more_than_the_line_drew(self):
        line = self.drawn()
        with self.assertRaisesMessage(ValidationError, "so 100 can come back, not 150"):
            self.back(line, "150").post()
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("1900"))
        self.assertEqual(self.balance(self.wip), Decimal("10000"))

    def test_not_the_same_kilos_twice(self):
        line = self.drawn()
        self.back(line, "100").post()
        with self.assertRaisesMessage(ValidationError, "100 has come back against it"):
            self.back(line, "100").post()
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("2000"))
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_not_off_a_voided_issue(self):
        line = self.drawn()
        line.issue.void(TODAY)
        with self.assertRaisesMessage(ValidationError, "was voided"):
            self.back(line, "100").post()
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("2000"))
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_not_off_a_draft(self):
        draft = self.issue(self.job, [(self.virgin, "100")])
        with self.assertRaisesMessage(ValidationError, "is not posted"):
            self.back(draft.lines.get(), "100").post()
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("2000"))

    def test_not_off_another_return(self):
        line = self.drawn()
        first = self.back(line, "40")
        first.post()
        with self.assertRaisesMessage(ValidationError, "is itself a return"):
            self.back(first.lines.get(), "40").post()
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("1940"))
        self.assertEqual(self.balance(self.wip), Decimal("6000"))

    def test_not_another_material(self):
        # Ten kilos of masterbatch at 200 drawn; filler is not what went out.
        line = self.drawn(self.colour, "10")
        with self.assertRaisesMessage(ValidationError, "that line drew MB-WHITE"):
            self.back(line, "10", item=self.filler).post()
        self.assertEqual(self.filler.on_hand_at(self.plant), Decimal("300"))

    def test_not_out_of_another_batch(self):
        Item.objects.filter(pk=self.virgin.pk).update(tracking=TrackingMode.LOT)
        self.virgin.refresh_from_db()
        first, second = (Lot.objects.create(item=self.virgin, code=code)
                         for code in ("PP-A", "PP-B"))
        for lot in (first, second):
            self.stock(self.virgin, "100", "100", lot=lot)
        drawn = self.issue_with_lots(self.job, [(self.virgin, "50", first)])
        drawn.post()
        back = MaterialIssue.objects.create(
            work_order=self.job, direction=IssueDirection.RETURN, issue_date=TODAY,
            warehouse=self.plant,
        )
        MaterialIssueLine.objects.create(
            issue=back, item=self.virgin, quantity=Decimal("50"), uom=self.kg,
            lot=second, returns_line=drawn.lines.get(), line_number=1,
        )
        with self.assertRaisesMessage(ValidationError, "out of the batch it drew it from"):
            back.post()
        self.assertEqual(second.on_hand_at(self.plant), Decimal("100"))

    def test_in_parts_up_to_what_it_drew(self):
        line = self.drawn()
        self.back(line, "40").post()
        self.back(line, "60").post()
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("2000"))
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertEqual(self.job.material_cost(), Decimal("0"))

    def test_a_voided_return_gives_its_kilos_back_to_the_line(self):
        line = self.drawn()
        mistaken = self.back(line, "100")
        mistaken.post()
        mistaken.void(TODAY)
        self.back(line, "100").post()
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("2000"))
        self.assertEqual(self.balance(self.wip), Decimal("0"))


class BookedInAnotherUnitOfWeightTests(RunTestCase):
    """
    Found by probing: a document in tonnes on a run kept in kilogrammes
    wrote its stock movement with the quantity in tonnes and the cost per
    kilogramme, and the ledger restated the pair as if the cost were per
    tonne. Half a tonne of tape went into the inventory account at
    45,855.67 and onto the shelf at 45.86; 20 kg of regrind weighed as
    0.02 t was valued as 0.02 kg, at 1.20 where 1,200 was due.
    """

    def setUp(self):
        super().setUp()
        self.tonne = UnitOfMeasure.objects.create(
            code="t", name="Tonne", category=UnitOfMeasureCategory.WEIGHT,
            base_unit=self.kg, conversion_factor=Decimal("1000"),
        )
        self.job = self.order()
        self.job.release(TODAY)

    def shelf(self, item):
        return item.valuation_at(self.plant)[1].quantize(Decimal("0.01"))

    def test_output_goes_on_the_shelf_at_what_the_ledger_took(self):
        self.full_issue(self.job).post()
        ledger = self.balance(self.inventory)
        self.produce(self.job, "0.5", uom=self.tonne).post()
        # 500 kg at the planned 91.711340.
        self.assertEqual(self.balance(self.inventory) - ledger, Decimal("45855.67"))
        self.assertEqual(self.shelf(self.tape), Decimal("45855.67"))
        self.assertEqual(self.tape.on_hand_at(self.plant), Decimal("500"))

    def test_voiding_it_takes_back_the_kilos_at_their_cost(self):
        self.full_issue(self.job).post()
        entry = self.produce(self.job, "0.5", uom=self.tonne)
        entry.post()
        entry.void(TODAY)
        back = StockMovement.objects.filter(item=self.tape).order_by("-id").first()
        self.assertEqual((back.quantity, back.unit_cost), (Decimal("-500"), Decimal("91.71134")))
        self.assertEqual(self.tape.on_hand_at(self.plant), Decimal("0"))

    def test_a_byproduct_is_valued_in_the_unit_it_was_weighed_in(self):
        entry = self.produce(self.job, "0")
        ProductionByproduct.objects.create(
            entry=entry, item=self.regrind, quantity=Decimal("0.02"), uom=self.tonne,
            line_number=1,
        )
        ledger, before = self.balance(self.inventory), self.shelf(self.regrind)
        entry.post()
        # 20 kg at the standard 60.
        self.assertEqual(self.balance(self.inventory) - ledger, Decimal("1200"))
        self.assertEqual(self.shelf(self.regrind) - before, Decimal("1200.00"))
        self.assertEqual(entry.byproducts.get().unit_value, Decimal("60"))

    def test_a_return_comes_back_onto_the_shelf_at_what_it_went_out_at(self):
        line = self.issue(self.job, [(self.virgin, "100")])
        line.post()
        back = MaterialIssue.objects.create(
            work_order=self.job, direction=IssueDirection.RETURN, issue_date=TODAY,
            warehouse=self.plant,
        )
        MaterialIssueLine.objects.create(
            issue=back, item=self.virgin, quantity=Decimal("0.04"), uom=self.tonne,
            returns_line=line.lines.get(), line_number=1,
        )
        ledger, before = self.balance(self.inventory), self.shelf(self.virgin)
        back.post()
        # 40 kg at the 100 it went out at.
        self.assertEqual(self.balance(self.inventory) - ledger, Decimal("4000"))
        self.assertEqual(self.shelf(self.virgin) - before, Decimal("4000.00"))

    def test_an_issue_records_its_kilos_at_their_cost(self):
        document = MaterialIssue.objects.create(
            work_order=self.job, issue_date=TODAY, warehouse=self.plant,
        )
        MaterialIssueLine.objects.create(
            issue=document, item=self.virgin, quantity=Decimal("0.1"), uom=self.tonne,
            line_number=1,
        )
        document.post()
        moved = document.lines.get().stock_movement
        self.assertEqual((moved.quantity, moved.unit_cost), (Decimal("-100"), Decimal("100")))
        self.assertEqual(self.balance(self.wip), Decimal("10000"))


class ARunKeepsItsWorkInProgressAccountTests(RunTestCase):
    """
    Found by probing: the account could move while only a closed run
    existed, and reopening reversed the close onto the old account while
    the run's next output and close went to the new one. Old work in
    progress ended 93,195.89, new -93,195.89, and the order said nothing
    was left.
    """

    def setUp(self):
        super().setUp()
        self.moved_to = Account.objects.create(code="1251", name="Work in progress, new",
                                               account_type=AccountType.ASSET)

    def move(self):
        settings = ManufacturingSettings.get()
        settings.wip_account = self.moved_to
        settings.save()

    def test_reopened_after_the_move_it_clears_where_it_was_kept(self):
        job = self.order()
        job.release(TODAY)
        self.full_issue(job).post()
        job.close(TODAY)
        self.move()
        job.reopen(TODAY)
        self.produce(job, "1000", byproducts=[(self.regrind, "24.742268")]).post()
        job.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertEqual(self.balance(self.moved_to), Decimal("0"))

    def test_released_after_the_move_it_keeps_the_new_one(self):
        self.move()
        job = self.order()
        job.release(TODAY)
        self.assertEqual(job.wip_account, self.moved_to)
        self.issue(job, [(self.virgin, "100")]).post()
        self.assertEqual(self.balance(self.moved_to), Decimal("10000"))
        self.assertEqual(self.balance(self.wip), Decimal("0"))


@tag("migration")
class RunsKeepTheAccountTheyWereReleasedOnTests(TransactionTestCase):
    """The upgrade freezes the present account onto runs released or closed."""

    before = [("manufacturing", "0077_maintenance_completion_withdrawn"),
              ("accounting", "0023_account_holds_money")]
    after = [("manufacturing", "0078_workorder_wip_account")]

    def test_released_and_closed_runs_take_the_present_account(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        wip = apps.get_model("accounting", "Account").objects.create(
            code="1250", name="Work in progress", account_type="asset")
        apps.get_model("manufacturing", "ManufacturingSettings").objects.create(wip_account=wip)
        kg = apps.get_model("core", "UnitOfMeasure").objects.create(
            code="kg", name="kg", category="weight")
        tape = apps.get_model("inventory", "Item").objects.create(sku="T", name="T", uom=kg)
        bom = apps.get_model("manufacturing", "BillOfMaterials").objects.create(
            item=tape, quantity_produced=Decimal("100"), uom=kg)
        plant = apps.get_model("inventory", "Warehouse").objects.create(code="P", name="P")
        runs = {
            status: apps.get_model("manufacturing", "WorkOrder").objects.create(
                item=tape, bom=bom, quantity_ordered=Decimal("10"), uom=kg,
                warehouse=plant, status=status).pk
            for status in ("draft", "released", "closed", "cancelled")
        }
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        apps = executor.loader.project_state(self.after).apps
        kept = dict(apps.get_model("manufacturing", "WorkOrder").objects.values_list(
            "status", "wip_account_id"))
        self.assertEqual(kept, {"draft": None, "released": wip.pk, "closed": wip.pk,
                                "cancelled": None})
        self.assertEqual(len(runs), 4)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


class AVoidTakesOffWhatTheShelfGivesUpTests(RunTestCase):
    """
    Found by probing. A void took goods back off the shelf at what they
    went on at, while the stock ledger took what removing them takes off
    a shelf whose average had moved; the inventory account and the shelf
    parted for good: -20,855.67 on output, -390.76 on its regrind, 816.33
    on a return. Each void now takes off what the shelf gives up and
    books the difference from the posted figure to material variance.
    """

    def setUp(self):
        super().setUp()
        self.job = self.order()
        self.job.release(TODAY)

    def shelf(self, *items):
        return sum((item.valuation_at(self.plant)[1] for item in items), Decimal("0"))

    def voided(self, document, *items):
        ledger, shelf = self.balance(self.inventory), self.shelf(*items)
        document.void(TODAY)
        return (self.balance(self.inventory) - ledger,
                (self.shelf(*items) - shelf).quantize(Decimal("0.01")))

    def test_output_onto_a_shelf_that_averages_less(self):
        # 1,000 kg already there at 50; the run's 1,000 at 91.71134 make
        # the average 70.85567, so taking 1,000 back off removes 70,855.67.
        self.stock(self.tape, "1000", "50")
        self.full_issue(self.job).post()
        entry = self.produce(self.job, "1000")
        entry.post()
        self.assertEqual(self.voided(entry, self.tape),
                         (Decimal("-70855.67"), Decimal("-70855.67")))
        self.assertEqual(self.balance(self.variance), Decimal("-20855.67"))
        self.assertAlmostEqual(self.balance(self.wip), Decimal("93195.89"), places=2)
        self.assertIsNotNone(entry.void_variance_entry)

    def test_its_regrind_too(self):
        # 500 kg more regrind at 20 make it average 40; after the issue and
        # 20 kg back at the standard 60, taking the 20 back off removes
        # 809.24 where 1,200 went on.
        self.stock(self.regrind, "500", "20")
        self.full_issue(self.job).post()
        entry = self.produce(self.job, "500", byproducts=[(self.regrind, "20")])
        entry.post()
        self.assertEqual(self.voided(entry, self.tape, self.regrind),
                         (Decimal("-46664.91"), Decimal("-46664.91")))
        self.assertEqual(self.balance(self.variance), Decimal("-390.76"))

    def test_a_return_onto_a_shelf_that_averages_more(self):
        # 40 kg came back at the 100 it went out at; 1,000 more at 160 make
        # the average 120.40816, so taking it off again removes 4,816.33.
        drawn = self.issue(self.job, [(self.virgin, "100")])
        drawn.post()
        self.stock(self.virgin, "1000", "160")
        back = self.issue(self.job, [(self.virgin, "40", drawn.lines.get())],
                          direction=IssueDirection.RETURN)
        back.post()
        self.assertEqual(self.voided(back, self.virgin),
                         (Decimal("-4816.33"), Decimal("-4816.33")))
        self.assertEqual(self.balance(self.variance), Decimal("816.33"))

    def test_where_the_shelf_has_not_moved_there_is_nothing_to_book(self):
        self.full_issue(self.job).post()
        entry = self.produce(self.job, "1000")
        entry.post()
        self.assertEqual(self.voided(entry, self.tape),
                         (Decimal("-91711.34"), Decimal("-91711.34")))
        self.assertIsNone(entry.void_variance_entry)
        self.assertEqual(self.balance(self.variance), Decimal("0"))


class SettingsThatWereNeverConfiguredTests(RunTestCase):
    def test_issuing_with_no_work_in_progress_account(self):
        settings = ManufacturingSettings.get()
        settings.wip_account = None
        settings.save()
        order = self.order()
        order.release(TODAY)
        document = self.issue(order, [(self.virgin, "10")])
        with self.assertRaises(ValidationError) as caught:
            document.post()
        self.assertIn("work in progress account", str(caught.exception))

    def test_scrapping_with_no_scrap_account(self):
        settings = ManufacturingSettings.get()
        settings.scrap_account = None
        settings.save()
        order = self.order()
        order.release(TODAY)
        self.full_issue(order).post()
        entry = self.produce(order, "900", scrapped="100")
        with self.assertRaises(ValidationError) as caught:
            entry.post()
        self.assertIn("scrap account", str(caught.exception))


class NothingMovesOnAClosedRunTests(RunTestCase):
    """
    A close sends whatever is left in work in progress to variance and
    nobody looks at the order again. A void against it afterwards puts
    money back into an account that is supposed to be empty, where it
    stays — which is the one balance this module exists to prevent.
    Found by probing; the suite was green.
    """

    def setUp(self):
        super().setUp()
        self.run = self.order()
        self.run.release(TODAY)
        self.document = self.issue(self.run, [(self.virgin, "100")])
        self.document.post()

    def test_an_issue_cannot_be_voided_after_the_close(self):
        self.run.close(TODAY)
        with self.assertRaises(ValidationError) as caught:
            self.document.void(TODAY)
        self.assertIn("Reopen the order first", str(caught.exception))
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_production_cannot_be_voided_after_the_close(self):
        entry = self.produce(self.run, "100")
        entry.post()
        self.run.close(TODAY)
        with self.assertRaises(ValidationError) as caught:
            entry.void(TODAY)
        self.assertIn("Reopen the order first", str(caught.exception))
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_reopening_first_is_the_way_through(self):
        self.run.close(TODAY)
        self.run.reopen(TODAY)
        self.document.void(TODAY)
        self.run.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertEqual(self.balance(self.variance), Decimal("0"))
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("2000"))


class AShelfCannotGiveWhatItHasNotGotTests(RunTestCase):
    def test_issuing_more_polymer_than_is_in_the_yard(self):
        order = self.order()
        order.release(TODAY)
        document = self.issue(order, [(self.virgin, "5000")])
        with self.assertRaises(ValidationError) as caught:
            document.post()
        self.assertIn("Only 2000", str(caught.exception))
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("2000"))
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_a_warehouse_that_allows_it_still_may(self):
        self.plant.allow_negative_stock = True
        self.plant.save()
        order = self.order()
        order.release(TODAY)
        document = self.issue(order, [(self.virgin, "2500")])
        document.post()
        self.assertEqual(self.virgin.on_hand_at(self.plant), Decimal("-500"))


class OutputHasAnAllowanceTests(RunTestCase):
    def test_a_mistyped_quantity_is_refused(self):
        order = self.order("100")
        order.release(TODAY)
        self.issue(order, [(self.virgin, "80")]).post()
        entry = self.produce(order, "100000")
        with self.assertRaises(ValidationError) as caught:
            entry.post()
        self.assertIn("past", str(caught.exception))
        self.assertEqual(self.tape.on_hand_at(self.plant), Decimal("0"))

    def test_a_run_that_went_a_little_long_is_not(self):
        order = self.order("100")
        order.release(TODAY)
        self.issue(order, [(self.virgin, "80")]).post()
        self.produce(order, "108").post()
        self.assertEqual(self.tape.on_hand_at(self.plant), Decimal("108"))

    def test_the_allowance_counts_what_is_already_booked(self):
        order = self.order("100")
        order.release(TODAY)
        self.issue(order, [(self.virgin, "80")]).post()
        self.produce(order, "105").post()
        with self.assertRaises(ValidationError):
            self.produce(order, "20").post()

    def test_scrap_counts_towards_it_too(self):
        # Two hundred kilos came off the line; that a hundred of them
        # failed does not make the run shorter.
        order = self.order("100")
        order.release(TODAY)
        self.issue(order, [(self.virgin, "80")]).post()
        with self.assertRaises(ValidationError):
            self.produce(order, "100", scrapped="100").post()

    def test_a_plant_that_really_does_run_long_says_so(self):
        order = self.order("100")
        order.over_production_percent = Decimal("100")
        order.save()
        order.release(TODAY)
        self.issue(order, [(self.virgin, "160")]).post()
        self.produce(order, "200").post()
        self.assertEqual(self.tape.on_hand_at(self.plant), Decimal("200"))


class APlanThatPricesSomethingAtNothingIsNotAPlanTests(RunTestCase):
    def test_a_material_never_bought_and_with_no_standard(self):
        ghost = Item.objects.create(sku="GHOST", name="Never bought", uom=self.kg)
        BomComponent.objects.create(
            bom=self.bom, item=ghost, quantity=Decimal("5"), uom=self.kg,
            waste_percent=Decimal("3"), line_number=9,
        )
        order = self.order()
        with self.assertRaises(ValidationError) as caught:
            order.release(TODAY)
        self.assertIn("as though it were free", str(caught.exception))

    def test_a_standard_cost_answers_it(self):
        ghost = Item.objects.create(
            sku="GHOST", name="Never bought", uom=self.kg,
            standard_cost=Decimal("500"),
        )
        BomComponent.objects.create(
            bom=self.bom, item=ghost, quantity=Decimal("5"), uom=self.kg,
            waste_percent=Decimal("3"), line_number=9,
        )
        order = self.order()
        order.release(TODAY)
        # 5 / 0.97 x 10 = 51.546392 kg at 500 = 25,773.20 on top of the
        # 91,711.34 the rest of the blend came to.
        self.assertAlmostEqual(
            order.planned_unit_cost, Decimal("117.484536"), places=4
        )


class AShareValuedByproductDoesNotDriftThroughTheRunTests(RunTestCase):
    """
    Valuing a by-product at a share of what has been issued so far means
    the same regrind comes back at one price on Tuesday and at twice
    that on Thursday, because more has been issued by then. The share is
    of the planned material cost, which was frozen at release.
    """

    def setUp(self):
        super().setUp()
        row = self.bom.byproducts.get()
        row.valuation = ByproductValuation.SHARE
        row.cost_share_percent = Decimal("5")
        row.save()

    def test_two_entries_value_it_the_same(self):
        order = self.order()
        order.release(TODAY)
        self.issue(order, [(self.virgin, "400")]).post()
        first = self.produce(order, "500", byproducts=[(self.regrind, "12")])
        first.post()
        self.issue(order, [(self.virgin, "373.1959")]).post()
        second = self.produce(order, "500", byproducts=[(self.regrind, "12")])
        second.post()
        self.assertEqual(
            first.byproducts.get().unit_value,
            second.byproducts.get().unit_value,
        )

    def test_and_it_is_a_share_of_the_plan(self):
        order = self.order()
        order.release(TODAY)
        # 5% of the 93,195.876 the blend was planned to cost, spread
        # over the 24.742268 kg of regrind the run was expected to give
        # back: 4,659.79 / 24.742268 = 188.333318 a kilo.
        self.assertAlmostEqual(
            order.planned_material_cost, Decimal("93195.876289"), places=4
        )
        self.issue(order, [(self.virgin, "400")]).post()
        entry = self.produce(order, "500", byproducts=[(self.regrind, "12")])
        entry.post()
        self.assertAlmostEqual(
            entry.byproducts.get().unit_value, Decimal("188.333318"), places=4
        )


class OutputBookedBeforeMaterialIsAllowedTests(RunTestCase):
    """
    Deliberately not refused. A shift books what it made and the store's
    paperwork catches up tomorrow; refusing would stop the plant to suit
    the ledger. What it must not do is hide — the credit sits in
    variance until the material arrives to offset it.
    """

    def test_it_shows_as_a_credit_rather_than_disappearing(self):
        order = self.order()
        order.release(TODAY)
        self.produce(order, "1000").post()
        self.assertAlmostEqual(
            order.wip_balance(), Decimal("-91711.34"), places=2
        )
        order.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertAlmostEqual(
            self.balance(self.variance), Decimal("-91711.34"), places=2
        )


class AnEntryBalancesByConstructionTests(RunTestCase):
    """
    Rounding a total and rounding its parts are different numbers. A
    blend of five materials whose values each carry a fraction of a
    paisa sums to one figure and rounds to another, and an entry built
    from both is out by a paisa and refused by the ledger.

    The suite did not catch this. Its prices were round; the first run
    with real ones in it failed on the first issue.
    """

    def setUp(self):
        super().setUp()
        # Prices that do not divide evenly into the cent.
        self.stock(self.virgin, "20000", "98.5017")
        self.stock(self.filler, "20000", "31.2033")
        self.stock(self.colour, "20000", "182.0071")
        self.stock(self.regrind, "20000", "58.0049")

    def test_an_issue_of_five_awkward_materials_posts(self):
        order = self.order("4000")
        order.release(TODAY)
        document = self.full_issue(order)
        document.post()
        self.assertIsNotNone(document.journal_entry)
        self.assertTrue(document.journal_entry.is_balanced())

    def test_what_is_frozen_is_what_the_ledger_took(self):
        # posted_value has to be the figure in the journal, not the
        # unrounded sum it was worked out from, or the work-in-progress
        # balance and the account drift apart by a paisa a document.
        order = self.order("4000")
        order.release(TODAY)
        document = self.full_issue(order)
        document.post()
        self.assertEqual(document.posted_value, self.balance(self.wip))

    def test_and_the_whole_run_still_clears(self):
        order = self.order("4000")
        order.release(TODAY)
        self.full_issue(order).post()
        self.produce(
            order, "3960", scrapped="18",
            byproducts=[(self.regrind, "103.4")],
        ).post()
        order.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))


class AClosedRunIsHoldingNothingTests(RunTestCase):
    """
    Found by running it. A closed order went on reporting the figure it
    had sent to variance, so the admin showed 15,942 sitting in work in
    progress for a run whose work in progress was empty.
    """

    def setUp(self):
        super().setUp()
        self.run = self.order()
        self.run.release(TODAY)
        self.full_issue(self.run).post()

    def test_the_reported_balance_follows_the_account(self):
        self.assertAlmostEqual(
            self.run.wip_balance(), self.balance(self.wip), places=2
        )
        self.run.close(TODAY)
        self.assertEqual(self.run.wip_balance(), Decimal("0"))
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_but_what_it_ate_is_still_readable(self):
        self.run.close(TODAY)
        self.assertAlmostEqual(
            self.run.unaccounted(), Decimal("93195.888"), places=2
        )

    def test_reopening_puts_the_balance_back(self):
        self.run.close(TODAY)
        self.run.reopen(TODAY)
        self.assertAlmostEqual(
            self.run.wip_balance(), self.balance(self.wip), places=2
        )
        self.assertGreater(self.run.wip_balance(), Decimal("0"))


class APostedDocumentsLinesAreFrozenTooTests(RunTestCase):
    """
    Guarding the header and leaving the lines editable is the shape this
    project keeps copying. `adjustments.py` and `transfers.py` both
    refuse a line on a posted document; these did not, so a posted
    issue's quantity could be retyped and the ledger would stay exactly
    where it was. Found by looking at the admin page, not by a test.
    """

    def setUp(self):
        super().setUp()
        self.run = self.order()
        self.run.release(TODAY)

    def test_a_line_on_a_posted_issue(self):
        document = self.issue(self.run, [(self.virgin, "100")])
        document.post()
        line = document.lines.get()
        line.quantity = Decimal("999")
        with self.assertRaises(ValidationError):
            line.save()
        line.refresh_from_db()
        self.assertEqual(line.quantity, Decimal("100"))
        self.assertAlmostEqual(self.run.material_cost(), Decimal("10000"), places=2)

    def test_deleting_one(self):
        document = self.issue(self.run, [(self.virgin, "100")])
        document.post()
        with self.assertRaises(ValidationError):
            document.lines.get().delete()

    def test_but_an_unposted_one_is_still_a_draft(self):
        document = self.issue(self.run, [(self.virgin, "100")])
        line = document.lines.get()
        line.quantity = Decimal("120")
        line.save()
        self.assertEqual(document.lines.get().quantity, Decimal("120"))

    def test_a_byproduct_on_a_posted_entry(self):
        self.full_issue(self.run).post()
        entry = self.produce(
            self.run, "1000", byproducts=[(self.regrind, "24.7423")]
        )
        entry.post()
        row = entry.byproducts.get()
        row.quantity = Decimal("500")
        with self.assertRaises(ValidationError):
            row.save()

    def test_a_requirement_frozen_at_release(self):
        component = self.run.components.first()
        component.quantity_required = Decimal("1")
        with self.assertRaises(ValidationError) as caught:
            component.save()
        self.assertIn("frozen when it was released", str(caught.exception))
