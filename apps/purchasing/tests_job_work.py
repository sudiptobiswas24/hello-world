"""
Job work against a bill of materials.

A woven sack plant sends fabric out to be printed and gets printed
fabric back. What goes into it is not a list somebody types onto a
purchase order — it is the bill of materials, which already says so to
six decimal places and which moves when the customer moves the
specification. A typed copy is a stored derived fact, and it goes stale
without saying anything.

The one subtlety is the unit. A bill of materials is written per
thousand sacks because that is how the shop floor talks;
`quantity_per` has always been per ONE stocking unit of the finished
item. The division happens once, here, rather than being multiplied out
at every receipt.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory
from apps.inventory.models import Item
from apps.manufacturing.bom import BillOfMaterials, BomComponent

from .models import PurchaseOrder, PurchaseOrderLine, SubcontractComponent
from .tests_subcontract import SubcontractTestCase


class JobWorkTestCase(SubcontractTestCase):
    def setUp(self):
        super().setUp()
        self.kg = UnitOfMeasure.objects.create(
            code="kg", name="Kilogram", category=UnitOfMeasureCategory.WEIGHT
        )
        self.pcs = UnitOfMeasure.objects.create(
            code="pcs", name="Pieces", category=UnitOfMeasureCategory.COUNT
        )
        self.fabric = Item.objects.create(sku="FAB", name="Woven fabric", uom=self.kg)
        self.ink = Item.objects.create(sku="INK", name="Flexo ink", uom=self.kg)
        self.thread = Item.objects.create(sku="THR", name="Thread", uom=self.kg)
        self.sack = Item.objects.create(sku="SACK", name="Printed sack", uom=self.pcs)
        self.stock(self.fabric, "20000", "92")
        self.stock(self.ink, "500", "620")
        self.stock(self.thread, "500", "146")

        # Written per thousand sacks, which is how the floor talks.
        self.bom = BillOfMaterials.objects.create(
            item=self.sack, name="Printed sack", quantity_produced=Decimal("1000"),
            uom=self.pcs,
        )
        BomComponent.objects.create(
            bom=self.bom, item=self.fabric, quantity=Decimal("110.236280"),
            uom=self.kg, waste_percent=Decimal("2.5"), line_number=1,
        )
        BomComponent.objects.create(
            bom=self.bom, item=self.ink, quantity=Decimal("7.2"), uom=self.kg,
            waste_percent=Decimal("2.5"), line_number=2,
        )
        BomComponent.objects.create(
            bom=self.bom, item=self.thread, quantity=Decimal("1.2"), uom=self.kg,
            waste_percent=Decimal("2.5"), line_number=3,
        )

    def job_work(self, quantity="50000", bom=None, item=None, confirm=False):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1),
            subcontract_warehouse=self.subcontractor,
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=item or self.sack, uom=self.pcs,
            quantity=Decimal(quantity), unit_price=Decimal("1.40"),
            bom=self.bom if bom is None else bom,
        )
        if confirm:
            order.confirm()
        return order, line


class TheComponentsComeOffTheBomTests(JobWorkTestCase):
    def test_a_bill_written_per_thousand_is_divided_down_once(self):
        _order, line = self.job_work()
        rows = {row.item.sku: row.quantity_per for row in line.components.all()}
        # 110.236280 kg of fabric per thousand sacks, 2.5% lost in
        # conversion: 110.236280 / 0.975 = 113.062851 kg, over a
        # thousand sacks = 0.113063 kg a sack.
        self.assertEqual(rows["FAB"], Decimal("0.113063"))
        # 7.2 / 0.975 / 1000
        self.assertEqual(rows["INK"], Decimal("0.007385"))
        # 1.2 / 0.975 / 1000 — the row four decimal places used to lose
        # a fortieth of.
        self.assertEqual(rows["THR"], Decimal("0.001231"))

    def test_the_waste_allowance_is_in_there(self):
        # The job worker is sent what will be consumed, not what ends up
        # in the sack. The offcut is real fabric and somebody paid for it.
        _order, line = self.job_work()
        net = Decimal("110.236280") / Decimal("1000")
        self.assertGreater(line.components.get(item=self.fabric).quantity_per, net)

    def test_moving_the_specification_moves_an_open_order(self):
        _order, line = self.job_work()
        heavier = self.bom.components.get(item=self.fabric)
        heavier.quantity = Decimal("125")
        heavier.save()
        line.save()
        self.assertEqual(
            line.components.get(item=self.fabric).quantity_per,
            Decimal("0.128205"),
        )

    def test_a_component_the_product_stopped_using_disappears(self):
        _order, line = self.job_work()
        self.assertTrue(line.components.filter(item=self.ink).exists())
        self.bom.components.get(item=self.ink).delete()
        line.save()
        self.assertFalse(line.components.filter(item=self.ink).exists())

    def test_a_computed_row_refuses_to_be_typed_over(self):
        _order, line = self.job_work()
        row = line.components.get(item=self.fabric)
        row.quantity_per = Decimal("0.5")
        with self.assertRaises(ValidationError) as caught:
            row.save()
        self.assertIn("Change the bill of materials", str(caught.exception))

    def test_nor_taken_off_on_its_own(self):
        _order, line = self.job_work()
        with self.assertRaises(ValidationError):
            line.components.get(item=self.ink).delete()

    def test_a_typed_line_is_still_a_typed_line(self):
        # The hand-written route has to go on working: plenty of job
        # work is a one-off nobody writes a bill of materials for.
        order = self.subcontract_order(confirm=False)
        row = self.line.components.get(item=self.frame)
        self.assertFalse(row.is_computed)
        row.quantity_per = Decimal("3")
        row.save()
        self.assertEqual(
            self.line.components.get(item=self.frame).quantity_per, Decimal("3")
        )


class WhatCanBeWrongIsAskedWhileItIsStillADraftTests(JobWorkTestCase):
    def test_a_bill_that_makes_something_else(self):
        other = BillOfMaterials.objects.create(
            item=self.fabric, quantity_produced=Decimal("100"), uom=self.kg
        )
        BomComponent.objects.create(
            bom=other, item=self.ink, quantity=Decimal("1"), uom=self.kg
        )
        with self.assertRaises(ValidationError) as caught:
            self.job_work(bom=other)
        self.assertIn("makes", str(caught.exception))

    def test_an_inactive_bill(self):
        self.bom.is_active = False
        self.bom.save()
        with self.assertRaises(ValidationError) as caught:
            self.job_work()
        self.assertIn("not active", str(caught.exception))

    def test_a_bill_with_no_components(self):
        empty = BillOfMaterials.objects.create(
            item=self.sack, version=2, is_default=False,
            quantity_produced=Decimal("1000"), uom=self.pcs,
        )
        with self.assertRaises(ValidationError) as caught:
            self.job_work(bom=empty)
        self.assertIn("nothing would be sent", str(caught.exception))

    def test_a_line_in_a_unit_the_components_are_not_written_per(self):
        # quantity_per is per one stocking unit of the finished item; a
        # line ordered in another unit would multiply every component by
        # a factor nobody wrote down.
        crate = UnitOfMeasure.objects.create(
            code="crate", name="Crate of 500", category=UnitOfMeasureCategory.COUNT,
            base_unit=self.pcs, conversion_factor=Decimal("500"),
        )
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1),
            subcontract_warehouse=self.subcontractor,
        )
        with self.assertRaises(ValidationError) as caught:
            PurchaseOrderLine.objects.create(
                order=order, item=self.sack, uom=crate, quantity=Decimal("100"),
                unit_price=Decimal("700"), bom=self.bom,
            )
        self.assertIn("Order it in", str(caught.exception))


class OnceItIsConfirmedItIsWhatWasSentTests(JobWorkTestCase):
    def test_a_later_change_to_the_bill_leaves_a_confirmed_order_alone(self):
        # The job worker was sent what the order said at the time, and
        # the receipt has to consume what was sent.
        _order, line = self.job_work(confirm=True)
        frozen = line.components.get(item=self.fabric).quantity_per
        heavier = self.bom.components.get(item=self.fabric)
        heavier.quantity = Decimal("125")
        heavier.save()
        line.refresh_from_db()
        line.save()
        self.assertEqual(
            line.components.get(item=self.fabric).quantity_per, frozen
        )

    def test_the_whole_chain_still_runs(self):
        order, line = self.job_work(quantity="1000", confirm=True)
        order.issue_components(self.warehouse)
        # 113.063 kg of fabric for a thousand sacks, on the job worker's
        # floor and still the plant's stock.
        self.assertAlmostEqual(
            self.fabric.on_hand_at(self.subcontractor),
            Decimal("113.063"), places=2,
        )
        self.receive(order, "1000")
        self.assertEqual(self.sack.on_hand_at(self.warehouse), Decimal("1000"))
        self.assertAlmostEqual(
            self.fabric.on_hand_at(self.subcontractor), Decimal("0"), places=2
        )


class ALineThatNoLongerNamesABillTests(JobWorkTestCase):
    """
    Found by probing. Computed rows refuse to be edited or deleted, so a
    line that stopped naming a bill of materials kept three of them for
    ever — still sent to the job worker, with nothing behind them.
    """

    def test_taking_the_bill_off_takes_the_rows_with_it(self):
        _order, line = self.job_work()
        self.assertEqual(line.components.count(), 3)
        line.bom = None
        line.save()
        self.assertEqual(line.components.count(), 0)
        self.assertFalse(line.is_subcontract())

    def test_typed_rows_on_the_same_line_are_left_alone(self):
        # A line can carry both: a bill of materials for what the
        # product is, and a typed row for something this job worker
        # needs that the product does not.
        _order, line = self.job_work()
        SubcontractComponent.objects.create(
            order_line=line, item=self.frame, quantity_per=Decimal("1")
        )
        line.bom = None
        line.save()
        self.assertEqual(line.components.count(), 1)
        self.assertEqual(line.components.get().item, self.frame)


class ABillIsNamedWhileTheOrderIsStillADraftTests(JobWorkTestCase):
    """
    Two ways a confirmed order used to end up lying about itself: named
    late, it claimed a bill of materials and had no components, so the
    receipt treated it as an ordinary purchase; swapped late, it pointed
    at one bill while carrying another's rows.
    """

    def test_it_cannot_be_named_after_the_order_is_confirmed(self):
        order, line = self.job_work(bom=False or None, confirm=False)
        line.bom = None
        line.save()
        order.confirm()
        line.refresh_from_db()
        line.bom = self.bom
        with self.assertRaises(ValidationError) as caught:
            line.save()
        self.assertIn("still a draft", str(caught.exception))

    def test_nor_swapped(self):
        _order, line = self.job_work(confirm=True)
        other = BillOfMaterials.objects.create(
            item=self.sack, version=2, is_default=False,
            quantity_produced=Decimal("1000"), uom=self.pcs,
        )
        BomComponent.objects.create(
            bom=other, item=self.ink, quantity=Decimal("99"), uom=self.kg
        )
        line.bom = other
        with self.assertRaises(ValidationError):
            line.save()

    def test_but_a_confirmed_line_can_still_be_saved_as_it_is(self):
        # Plenty of other code saves a line; only a change is refused.
        _order, line = self.job_work(confirm=True)
        line.description = "Two colour, reverse print"
        line.save()
        self.assertEqual(line.components.count(), 3)

    def test_a_component_that_rounds_to_nothing(self):
        # A raw database constraint used to surface here instead of a
        # sentence naming the component.
        trace = Item.objects.create(sku="TRACE", name="Tracer", uom=self.kg)
        BomComponent.objects.create(
            bom=self.bom, item=trace, quantity=Decimal("0.0001"),
            uom=self.kg, line_number=9,
        )
        with self.assertRaises(ValidationError) as caught:
            self.job_work()
        self.assertIn("rounds to nothing", str(caught.exception))
