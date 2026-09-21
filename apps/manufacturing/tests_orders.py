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
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import (
    Company,
    Currency,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse

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

    def stock(self, item, quantity, cost):
        """Put stock on the shelf without going through a document."""
        return StockMovement.objects.create(
            item=item, warehouse=self.plant, movement_type=MovementType.RECEIPT,
            uom=self.kg, quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )

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

    def produce(self, order, quantity, scrapped="0", byproducts=()):
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal(quantity),
            quantity_scrapped=Decimal(scrapped), uom=self.kg,
            work_centre=self.loom,
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
