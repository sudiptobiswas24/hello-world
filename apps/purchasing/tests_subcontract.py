"""
Subcontracting.

What this covers is the part that touches purchasing and the ledger:
components leaving, a finished item arriving, and its cost being what
the components cost plus what the vendor charged to assemble them. The
routing stays with the job worker and is theirs to run.

The components themselves are the other half, and they live in
`tests_job_work.py`: a bill of materials already says what goes into
the thing, and a hand-typed copy of it on a purchase order goes stale
the first time the specification moves.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import JournalLine
from apps.core.models import Company
from django.utils import timezone

from apps.inventory.models import Item, MovementType, StockMovement, Warehouse

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SubcontractComponent,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class SubcontractTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.subcontractor = Warehouse.objects.create(code="SUB", name="Acme Assembly")
        self.frame = Item.objects.create(sku="FRM", name="Frame", uom=self.uom)
        self.motor = Item.objects.create(sku="MTR", name="Motor", uom=self.uom)
        self.assembly = Item.objects.create(sku="ASM", name="Assembly", uom=self.uom)
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()
        self.stock(self.frame, "100", "8")
        self.stock(self.motor, "100", "12")

    def stock(self, item, quantity, cost, warehouse=None):
        StockMovement.objects.create(
            item=item, warehouse=warehouse or self.warehouse,
            movement_type=MovementType.RECEIPT, uom=item.uom, quantity=Decimal(quantity),
            unit_cost=Decimal(cost), occurred_at=timezone.now(),
        )

    def subcontract_order(self, quantity="10", service="5", warehouse=True, confirm=True):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1),
            subcontract_warehouse=self.subcontractor if warehouse else None,
        )
        self.line = PurchaseOrderLine.objects.create(
            order=order, item=self.assembly, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(service),
        )
        SubcontractComponent.objects.create(
            order_line=self.line, item=self.frame, quantity_per=Decimal("1")
        )
        SubcontractComponent.objects.create(
            order_line=self.line, item=self.motor, quantity_per=Decimal("2")
        )
        if confirm:
            order.confirm()
        return order

    def receive(self, order, quantity):
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 2, 1)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order.lines.first(),
            warehouse=self.warehouse, quantity_received=Decimal(quantity),
        )
        receipt.post()
        return receipt

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class ComponentIssueTests(SubcontractTestCase):
    def test_components_move_rather_than_leaving_the_company(self):
        """Stock at a subcontractor is still stock you own and still
        stock you can lose."""
        order = self.subcontract_order("10")
        before = self.balance(self.inventory)

        order.issue_components(self.warehouse)

        self.assertEqual(self.frame.on_hand_at(self.warehouse), Decimal("90"))
        self.assertEqual(self.frame.on_hand_at(self.subcontractor), Decimal("10"))
        self.assertEqual(self.motor.on_hand_at(self.subcontractor), Decimal("20"))
        self.assertEqual(self.balance(self.inventory), before)

    def test_the_value_goes_with_them(self):
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)

        self.assertEqual(self.frame.stock_value_at(self.subcontractor), Decimal("80.00"))
        self.assertEqual(self.motor.stock_value_at(self.subcontractor), Decimal("240.00"))

    def test_a_draft_order_cannot_issue(self):
        order = self.subcontract_order(confirm=False)
        with self.assertRaisesMessage(ValidationError, "Only a confirmed order"):
            order.issue_components(self.warehouse)

    def test_it_needs_somewhere_to_send_them(self):
        order = self.subcontract_order(warehouse=False)
        with self.assertRaisesMessage(ValidationError, "subcontract warehouse"):
            order.issue_components(self.warehouse)

    def test_an_order_with_no_components_has_nothing_to_issue(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1),
            subcontract_warehouse=self.subcontractor,
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("1"), unit_price=Decimal("5"),
        )
        order.confirm()
        with self.assertRaisesMessage(ValidationError, "no components to issue"):
            order.issue_components(self.warehouse)


class SubcontractReceiptTests(SubcontractTestCase):
    def test_the_finished_item_carries_the_component_cost(self):
        """Valuing it at the vendor's charge alone would report a part
        built from 32 of components as worth the 5 of labour."""
        order = self.subcontract_order("10", service="5")
        order.issue_components(self.warehouse)

        self.receive(order, "10")

        # frame 8 + two motors at 12 = 32, plus 5 of assembly = 37
        self.assertEqual(self.assembly.average_cost_at(self.warehouse), Decimal("37.0000"))
        self.assertEqual(self.assembly.stock_value_at(self.warehouse), Decimal("370.00"))

    def test_the_components_are_consumed(self):
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)

        self.receive(order, "10")

        self.assertEqual(self.frame.on_hand_at(self.subcontractor), Decimal("0"))
        self.assertEqual(self.motor.on_hand_at(self.subcontractor), Decimal("0"))

    def test_only_the_vendor_s_charge_reaches_the_ledger(self):
        """The component value has merely moved between items inside the
        same inventory account; posting it again would double it."""
        order = self.subcontract_order("10", service="5")
        order.issue_components(self.warehouse)
        before = self.balance(self.inventory)

        self.receive(order, "10")

        self.assertEqual(self.balance(self.inventory) - before, Decimal("50"))
        self.assertEqual(self.balance(self.grni), Decimal("-50"))

    def stock_value(self):
        return sum(
            item.stock_value_at(warehouse)
            for item in (self.frame, self.motor, self.assembly)
            for warehouse in (self.warehouse, self.subcontractor)
        )

    def test_the_books_and_the_warehouse_move_together(self):
        """Subcontracting shuffles value between items and warehouses; the
        only thing that may change the total is the vendor's charge."""
        order = self.subcontract_order("10", service="5")
        stock_before, ledger_before = self.stock_value(), self.balance(self.inventory)

        order.issue_components(self.warehouse)
        self.assertEqual(self.stock_value(), stock_before)
        self.assertEqual(self.balance(self.inventory), ledger_before)

        self.receive(order, "10")

        self.assertEqual(self.stock_value() - stock_before, Decimal("50.00"))
        self.assertEqual(self.balance(self.inventory) - ledger_before, Decimal("50.00"))

    def test_a_partial_receipt_consumes_its_share(self):
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)

        self.receive(order, "4")

        self.assertEqual(self.frame.on_hand_at(self.subcontractor), Decimal("6"))
        self.assertEqual(self.motor.on_hand_at(self.subcontractor), Decimal("12"))
        self.assertEqual(self.assembly.on_hand_at(self.warehouse), Decimal("4"))

    def test_receiving_without_issuing_is_refused(self):
        """The subcontractor cannot have used what they were never sent."""
        order = self.subcontract_order("10")
        with self.assertRaisesMessage(ValidationError, "is short"):
            self.receive(order, "10")

    def test_a_missing_subcontract_warehouse_is_refused(self):
        order = self.subcontract_order("10", warehouse=False)
        with self.assertRaisesMessage(ValidationError, "nowhere to be consumed from"):
            self.receive(order, "10")

    def test_an_ordinary_line_is_untouched(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("5"),
        )
        order.confirm()
        self.receive(order, "10")

        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("5.0000"))


class ComponentsGoOutOnceTests(SubcontractTestCase):
    """Nothing asked whether they had gone: a second call sent them all again."""

    def test_a_second_issue_is_refused(self):
        order = self.subcontract_order()
        order.issue_components(self.warehouse)
        with self.assertRaisesMessage(ValidationError, "already been issued"):
            order.issue_components(self.warehouse)


class ComponentTopUpTests(SubcontractTestCase):
    """A second issue was refused outright, so components the subcontractor
    scrapped could never be replaced and the order never received."""

    def test_a_second_issue_sends_nothing_and_says_so(self):
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        with self.assertRaisesMessage(ValidationError, "already been issued"):
            order.issue_components(self.warehouse)
        self.assertEqual(self.frame.on_hand_at(self.subcontractor), Decimal("10"))
        self.assertEqual(self.motor.on_hand_at(self.subcontractor), Decimal("20"))

    def test_what_was_scrapped_is_topped_up_and_the_order_received(self):
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        # Two frames ruined at the subcontractor's.
        StockMovement.objects.create(
            item=self.frame, warehouse=self.subcontractor, movement_type=MovementType.ADJUSTMENT,
            uom=self.uom, quantity=Decimal("-2"), unit_cost=Decimal("8"), occurred_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "short 2"):
            self.receive(order, "10")
        order.issue_components(self.warehouse, quantities={self.frame: Decimal("2")})
        self.assertEqual(self.frame.on_hand_at(self.warehouse), Decimal("88"))
        self.receive(order, "10")
        self.assertEqual(self.assembly.on_hand_at(self.warehouse), Decimal("10"))
        self.assertEqual(self.frame.on_hand_at(self.subcontractor), Decimal("0"))

    def test_a_top_up_names_a_component_of_this_order(self):
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        with self.assertRaisesMessage(ValidationError, "not a component"):
            order.issue_components(self.warehouse, quantities={self.assembly: Decimal("1")})
        with self.assertRaisesMessage(ValidationError, "above nothing"):
            order.issue_components(self.warehouse, quantities={self.frame: Decimal("0")})


class ComponentsComeBackAtWhatTheyCostTests(SubcontractTestCase):
    """
    Thirty frames at the subcontractor worth 100 (ten at 4, twenty at 3),
    consumed by thirty assemblies at a four-place 3.3333 a frame. Sent
    back, the assemblies returned their frames at that rate: 99.999 of the
    100 that went into them.
    """

    def test_returned_assemblies_put_their_frames_back_at_what_they_took(self):
        self.stock(self.frame, "10", "4", warehouse=self.subcontractor)
        self.stock(self.frame, "20", "3", warehouse=self.subcontractor)
        self.stock(self.motor, "60", "12", warehouse=self.subcontractor)
        receipt = self.receive(self.subcontract_order("30"), "30")
        receipt.create_return(debit_bills=False)
        self.assertEqual(self.frame.valuation_at(self.subcontractor)[1].quantize(Decimal("0.0001")),
                         Decimal("100.0000"))


class BatchKeptComponentsTests(SubcontractTestCase):
    """
    Audit, 9 October: a frame kept by batch could not go to the
    subcontractor at all; the move named no batch and was refused. And
    everything a mandatory inspection plan covers is kept by batch. The
    batches go, are consumed and come back as a delivery picks them.
    """

    def setUp(self):
        super().setUp()
        from apps.inventory.models import Lot

        Item.objects.filter(pk=self.frame.pk).update(tracking="lot")
        self.frame.refresh_from_db()
        StockMovement.objects.filter(item=self.frame).delete()
        self.lot_model = Lot

    def batch(self, code, quantity, expires=None):
        batch = self.lot_model.objects.create(item=self.frame, code=code, expires_on=expires)
        StockMovement.objects.create(item=self.frame, warehouse=self.warehouse,
                                     movement_type=MovementType.RECEIPT, uom=self.frame.uom,
                                     quantity=Decimal(quantity), unit_cost=Decimal("8"), lot=batch,
                                     occurred_at=timezone.now())
        return batch

    def test_a_batch_kept_component_is_sent_by_its_batch(self):
        batch = self.batch("F-1", "100")
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        self.assertEqual((batch.on_hand_at(self.subcontractor), batch.on_hand_at(self.warehouse)),
                         (Decimal("10"), Decimal("90")))
        self.assertEqual(self.frame.stock_value_at(self.subcontractor), Decimal("80.00"))

    def test_the_soonest_to_expire_goes_and_is_consumed(self):
        sooner = self.batch("F-1", "40", datetime.date(2026, 12, 31))
        later = self.batch("F-2", "60", datetime.date(2027, 6, 30))
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        self.assertEqual((sooner.on_hand_at(self.subcontractor), later.on_hand_at(self.subcontractor)),
                         (Decimal("10"), Decimal("0")))
        self.receive(order, "10")
        self.assertEqual(sooner.on_hand_at(self.subcontractor), Decimal("0"))
        # frame 8 + two motors at 12 + 5 of assembly
        self.assertEqual(self.assembly.average_cost_at(self.warehouse), Decimal("37.0000"))

    def test_a_batch_quality_holds_is_not_sent(self):
        from apps.quality.models import Characteristic, Inspection, InspectionPlan, PlanLine, Reading

        held = self.batch("F-1", "40", datetime.date(2026, 12, 31))
        good = self.batch("F-2", "60", datetime.date(2027, 6, 30))
        check = Characteristic.objects.create(code="FLAT", name="Flatness", uom=self.uom)
        plan = InspectionPlan.objects.create(item=self.frame, is_mandatory=False)
        line = PlanLine.objects.create(plan=plan, characteristic=check, lower_limit=Decimal("0"),
                                       upper_limit=Decimal("1"))
        inspection = Inspection.objects.create(lot=held, plan=plan, inspected_on=datetime.date(2026, 1, 2))
        Reading.objects.create(inspection=inspection, plan_line=line, value=Decimal("3"))
        inspection.post()
        self.subcontract_order("10").issue_components(self.warehouse)
        self.assertEqual((held.on_hand_at(self.subcontractor), good.on_hand_at(self.subcontractor)),
                         (Decimal("0"), Decimal("10")))

    def test_assemblies_sent_back_put_each_batch_back(self):
        """Ten frames from two batches, 4 and 6; five assemblies back put 2 and 3 back."""
        first = self.batch("F-1", "4", datetime.date(2026, 12, 31))
        second = self.batch("F-2", "96", datetime.date(2027, 6, 30))
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        receipt = self.receive(order, "10")
        self.assertEqual((first.on_hand_at(self.subcontractor), second.on_hand_at(self.subcontractor)),
                         (Decimal("0"), Decimal("0")))
        receipt.create_return(quantities={receipt.lines.get(): Decimal("5")}, debit_bills=False)
        self.assertEqual((first.on_hand_at(self.subcontractor), second.on_hand_at(self.subcontractor)),
                         (Decimal("2"), Decimal("3")))
        self.assertEqual(self.frame.stock_value_at(self.subcontractor), Decimal("40.00"))


class _SentBatches(SubcontractTestCase):
    """Frames kept by batch, 100 of F-1 at 8 in the store unless a test says otherwise."""

    def setUp(self):
        super().setUp()
        from apps.inventory.models import Lot

        Item.objects.filter(pk=self.frame.pk).update(tracking="lot")
        self.frame.refresh_from_db()
        StockMovement.objects.filter(item=self.frame).delete()
        self.lot_model = Lot

    def batch(self, code, quantity, expires=None, warehouse=None):
        made = self.lot_model.objects.create(item=self.frame, code=code, expires_on=expires)
        StockMovement.objects.create(item=self.frame, warehouse=warehouse or self.warehouse,
                                     movement_type=MovementType.RECEIPT, uom=self.frame.uom,
                                     quantity=Decimal(quantity), unit_cost=Decimal("8"), lot=made,
                                     occurred_at=timezone.now())
        return made

    def inspect(self, lot, value, on):
        from apps.quality.models import Inspection, Reading

        inspection = Inspection.objects.create(lot=lot, plan=self.plan, inspected_on=on)
        Reading.objects.create(inspection=inspection, plan_line=self.plan_line, value=Decimal(value))
        inspection.post()
        return inspection

    def mandatory_plan(self):
        from apps.quality.models import Characteristic, InspectionPlan, PlanLine

        check = Characteristic.objects.create(code="FLAT", name="Flatness", uom=self.uom)
        self.plan = InspectionPlan.objects.create(item=self.frame, is_mandatory=True)
        self.plan_line = PlanLine.objects.create(plan=self.plan, characteristic=check,
                                                 lower_limit=Decimal("0"), upper_limit=Decimal("1"))

    def sent_then_rejected(self):
        """F-1 passed on 1 January and sent; failed on 2 January, rejected under a mandatory plan."""
        self.mandatory_plan()
        sent = self.batch("F-1", "100")
        self.inspect(sent, "0.5", datetime.date(2026, 1, 1))
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        self.inspect(sent, "3", datetime.date(2026, 1, 2))
        return sent, order

    def receive_into(self, order, quantity, warehouse, lot=None):
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 2, 1))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.first(), lot=lot,
                                        warehouse=warehouse, quantity_received=Decimal(quantity))
        receipt.post()
        return receipt


class ComponentsNeverLeaveQuarantineTests(_SentBatches):
    """O147: issue_components gave the gate no warehouse; QA's frames went to the subcontractor."""

    def test_batches_in_a_quarantine_bay_are_not_sent(self):
        bay = Warehouse.objects.create(code="QA", name="Inspection bay", is_quarantine=True)
        self.batch("F-Q", "100", warehouse=bay)
        self.stock(self.motor, "100", "12", warehouse=bay)
        order = self.subcontract_order("10")
        with self.assertRaisesMessage(ValidationError, "holds goods awaiting inspection"):
            order.issue_components(bay)
        self.assertEqual((self.frame.on_hand_at(bay), self.frame.on_hand_at(self.subcontractor)),
                         (Decimal("100"), Decimal("0")))

    def test_what_is_not_kept_by_batch_is_not_sent_either(self):
        Item.objects.filter(pk=self.frame.pk).update(tracking="none")
        self.frame.refresh_from_db()
        bay = Warehouse.objects.create(code="QA", name="Inspection bay", is_quarantine=True)
        self.stock(self.frame, "100", "8", warehouse=bay)
        self.stock(self.motor, "100", "12", warehouse=bay)
        with self.assertRaisesMessage(ValidationError, "holds goods awaiting inspection"):
            self.subcontract_order("10").issue_components(bay)
        self.assertEqual(self.motor.on_hand_at(bay), Decimal("100"))


class AssembliesFromABatchHeldSinceTests(_SentBatches):
    """
    O135, O148 (owner's decision, item 22): ten assemblies back from the
    subcontractor, made from F-1 before F-1 was rejected, are received and
    held. Frame 8 + two motors at 12 + 5 of assembly: 37 each, 370.00.
    """

    def setUp(self):
        super().setUp()
        Item.objects.filter(pk=self.assembly.pk).update(tracking="lot")
        self.assembly.refresh_from_db()
        self.made = self.lot_model.objects.create(item=self.assembly, code="A-1")

    def test_the_receipt_is_recorded_and_the_batch_consumed_where_it_lies(self):
        sent, order = self.sent_then_rejected()
        self.receive_into(order, "10", self.warehouse, lot=self.made)
        self.assertEqual((sent.on_hand_at(self.subcontractor), self.motor.on_hand_at(self.subcontractor)),
                         (Decimal("0"), Decimal("0")))
        self.assertEqual(self.made.on_hand_at(self.warehouse), Decimal("10"))
        self.assertEqual(self.assembly.stock_value_at(self.warehouse), Decimal("370.00"))

    def test_the_assemblies_take_the_hold_until_inspected_themselves(self):
        from apps.quality.release import check_released

        _sent, order = self.sent_then_rejected()
        receipt = self.receive_into(order, "10", self.warehouse, lot=self.made)
        with self.assertRaisesMessage(ValidationError, "A-1 is held"):
            check_released(self.assembly, self.made, action="be shipped", warehouse=self.warehouse)
        (held,) = receipt.held_assemblies()
        self.assertEqual((held["lot"], held["item"]), ("A-1", str(self.assembly)))
        self.assertIn("F-1 is held", held["why"])

    def test_returned_to_the_subcontractor_they_put_back_what_they_took(self):
        sent, order = self.sent_then_rejected()
        receipt = self.receive_into(order, "10", self.warehouse, lot=self.made)
        receipt.create_return(debit_bills=False)
        self.assertEqual((sent.on_hand_at(self.subcontractor), self.motor.on_hand_at(self.subcontractor),
                          self.made.on_hand_at(self.warehouse)), (Decimal("10"), Decimal("20"), Decimal("0")))
        self.assertEqual(self.frame.stock_value_at(self.subcontractor), Decimal("80.00"))

    def test_a_batch_only_awaiting_inspection_still_bars_the_receipt(self):
        sent = self.batch("F-1", "100")
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        self.mandatory_plan()
        with self.assertRaisesMessage(ValidationError, "held by quality"):
            self.receive_into(order, "10", self.warehouse, lot=self.made)
        self.assertEqual(sent.on_hand_at(self.subcontractor), Decimal("10"))

    def test_the_answer_says_they_are_held_through_the_api(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        _sent, order = self.sent_then_rejected()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 2, 1))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.first(), lot=self.made,
                                        warehouse=self.warehouse, quantity_received=Decimal("10"))
        clerk = User.objects.create(username="stores")
        clerk.groups.add(Group.objects.get(name="Warehouse Staff"))
        client = APIClient()
        client.force_authenticate(clerk)
        response = client.post(f"/api/purchasing/goods-receipts/{receipt.pk}/post_receipt/")
        self.assertEqual(response.status_code, 200, response.content)
        (held,) = response.json()["held"]
        self.assertEqual(held["lot"], "A-1")
        self.assertIn("F-1 is held", held["why"])


class UntrackedAssembliesFromAHeldBatchTests(_SentBatches):
    """O135, O148: an assembly not kept by batch cannot carry the hold, so it is received into quarantine."""

    def test_a_store_is_refused_and_says_why(self):
        sent, order = self.sent_then_rejected()
        with self.assertRaisesMessage(ValidationError, "receive them into a quarantine warehouse"):
            self.receive_into(order, "10", self.warehouse)
        self.assertEqual(sent.on_hand_at(self.subcontractor), Decimal("10"))

    def test_received_into_quarantine_nothing_draws_them_and_rejected_they_go_back(self):
        from apps.quality.release import check_released

        bay = Warehouse.objects.create(code="QA", name="Inspection bay", is_quarantine=True)
        sent, order = self.sent_then_rejected()
        receipt = self.receive_into(order, "10", bay)
        self.assertEqual((self.assembly.on_hand_at(bay), sent.on_hand_at(self.subcontractor)),
                         (Decimal("10"), Decimal("0")))
        with self.assertRaisesMessage(ValidationError, "holds goods awaiting inspection"):
            check_released(self.assembly, None, action="be shipped", warehouse=bay)
        (held,) = receipt.held_assemblies()
        self.assertEqual(held["lot"], None)
        receipt.reject(note="Made from F-1, rejected", debit_bills=False)
        self.assertEqual((self.assembly.on_hand_at(bay), sent.on_hand_at(self.subcontractor),
                          self.motor.on_hand_at(self.subcontractor)), (Decimal("0"), Decimal("10"), Decimal("20")))


class ComponentsComeBackBatchByBatchTests(_SentBatches):
    """O149: each batch gets back what it gave, however the assemblies come back."""

    def test_returned_one_at_a_time_each_batch_gets_back_what_it_gave(self):
        first = self.batch("F-1", "1", datetime.date(2026, 12, 31))
        second = self.batch("F-2", "99", datetime.date(2027, 6, 30))
        order = self.subcontract_order("3")
        order.issue_components(self.warehouse)
        receipt = self.receive(order, "3")
        for _ in range(3):
            receipt.create_return(quantities={receipt.lines.get(): Decimal("1")}, debit_bills=False)
        self.assertEqual((first.on_hand_at(self.subcontractor), second.on_hand_at(self.subcontractor)),
                         (Decimal("1"), Decimal("2")))

    def test_three_of_ten_put_back_whole_frames_and_the_rest_the_remainder(self):
        first = self.batch("F-1", "4", datetime.date(2026, 12, 31))
        second = self.batch("F-2", "96", datetime.date(2027, 6, 30))
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        receipt = self.receive(order, "10")
        receipt.create_return(quantities={receipt.lines.get(): Decimal("3")}, debit_bills=False)
        self.assertEqual((first.on_hand_at(self.subcontractor), second.on_hand_at(self.subcontractor)),
                         (Decimal("1"), Decimal("2")))
        self.assertEqual(self.frame.stock_value_at(self.subcontractor), Decimal("24.00"))
        receipt.create_return(quantities={receipt.lines.get(): Decimal("7")}, debit_bills=False)
        self.assertEqual((first.on_hand_at(self.subcontractor), second.on_hand_at(self.subcontractor)),
                         (Decimal("4"), Decimal("6")))


class AssembliesAreNotPassedByTheirComponentsTests(_SentBatches):
    """
    The lineage carries a hold, never a pass: frames passed are not an
    assembly inspected. Ten assemblies of A-2 from F-1, passed on 1 January,
    under the assembly's own mandatory plan, wait for their own inspection.
    """

    def test_an_assembly_from_passed_batches_still_waits_for_its_own_inspection(self):
        from apps.quality.models import Characteristic, InspectionPlan, PlanLine
        from apps.quality.release import check_released

        Item.objects.filter(pk=self.assembly.pk).update(tracking="lot")
        self.assembly.refresh_from_db()
        made = self.lot_model.objects.create(item=self.assembly, code="A-2")
        self.mandatory_plan()
        sent = self.batch("F-1", "100")
        self.inspect(sent, "0.5", datetime.date(2026, 1, 1))
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        torque = Characteristic.objects.create(code="TRQ", name="Torque", uom=self.uom)
        own = InspectionPlan.objects.create(item=self.assembly, is_mandatory=True)
        PlanLine.objects.create(plan=own, characteristic=torque, lower_limit=Decimal("0"),
                                upper_limit=Decimal("1"))
        self.receive_into(order, "10", self.warehouse, lot=made)
        with self.assertRaisesMessage(ValidationError, "A-2 has not been inspected"):
            check_released(self.assembly, made, action="be shipped", warehouse=self.warehouse)


class EachOrderConsumesWhatWasSentOnItTests(_SentBatches):
    """
    Follow-up to O135: F-1 (sooner to expire, rejected after it went) sent 10
    for order A, F-2 (passed) 10 for order B. B received first consumed F-1,
    first expired over the whole bay: B's assemblies held, A's, made from
    F-1, released. Each order now consumes its own batches first.
    """

    def setUp(self):
        super().setUp()
        Item.objects.filter(pk=self.assembly.pk).update(tracking="lot")
        self.assembly.refresh_from_db()
        self.mandatory_plan()
        self.f1 = self.batch("F-1", "10", datetime.date(2026, 12, 31))
        self.f2 = self.batch("F-2", "10", datetime.date(2027, 6, 30))
        for batch in (self.f1, self.f2):
            self.inspect(batch, "0.5", datetime.date(2026, 1, 1))
        self.order_a = self.subcontract_order("10")
        self.order_a.issue_components(self.warehouse)
        self.order_b = self.subcontract_order("10")
        self.order_b.issue_components(self.warehouse)
        self.inspect(self.f1, "3", datetime.date(2026, 1, 2))
        self.lot_a = self.lot_model.objects.create(item=self.assembly, code="A-A")
        self.lot_b = self.lot_model.objects.create(item=self.assembly, code="A-B")

    def at_sub(self):
        return (self.f1.on_hand_at(self.subcontractor), self.f2.on_hand_at(self.subcontractor))

    def released(self, lot):
        from apps.quality.release import why_held

        return why_held(self.assembly, lot, warehouse=self.warehouse) == ""

    def test_the_order_received_first_consumes_its_own_batch(self):
        self.assertEqual(self.at_sub(), (Decimal("10"), Decimal("10")))
        receipt_b = self.receive_into(self.order_b, "10", self.warehouse, lot=self.lot_b)
        self.assertEqual(self.at_sub(), (Decimal("10"), Decimal("0")))
        receipt_a = self.receive_into(self.order_a, "10", self.warehouse, lot=self.lot_a)
        self.assertEqual(self.at_sub(), (Decimal("0"), Decimal("0")))
        self.assertEqual((self.released(self.lot_b), self.released(self.lot_a)), (True, False))
        self.assertEqual((receipt_b.held_assemblies(), len(receipt_a.held_assemblies())), ([], 1))

    def test_partial_receipts_in_turn_each_take_their_own(self):
        lot_a2 = self.lot_model.objects.create(item=self.assembly, code="A-A2")
        lot_b2 = self.lot_model.objects.create(item=self.assembly, code="A-B2")
        self.receive_into(self.order_a, "5", self.warehouse, lot=self.lot_a)
        self.assertEqual(self.at_sub(), (Decimal("5"), Decimal("10")))
        self.receive_into(self.order_b, "5", self.warehouse, lot=self.lot_b)
        self.assertEqual(self.at_sub(), (Decimal("5"), Decimal("5")))
        self.receive_into(self.order_a, "5", self.warehouse, lot=lot_a2)
        self.receive_into(self.order_b, "5", self.warehouse, lot=lot_b2)
        self.assertEqual(self.at_sub(), (Decimal("0"), Decimal("0")))
        self.assertEqual([self.released(lot) for lot in (self.lot_a, lot_a2, self.lot_b, lot_b2)],
                         [False, False, True, True])

    def test_returned_and_received_again_b_takes_its_own_batch_back(self):
        """A goods receipt has no void: a full return is how it is undone."""
        receipt_b = self.receive_into(self.order_b, "10", self.warehouse, lot=self.lot_b)
        receipt_b.create_return(debit_bills=False)
        self.assertEqual(self.at_sub(), (Decimal("10"), Decimal("10")))
        self.assertEqual(self.lot_b.on_hand_at(self.warehouse), Decimal("0"))
        again = self.lot_model.objects.create(item=self.assembly, code="A-B3")
        self.receive_into(self.order_b, "10", self.warehouse, lot=again)
        self.assertEqual(self.at_sub(), (Decimal("10"), Decimal("0")))
        self.assertTrue(self.released(again))

    def test_short_of_its_own_it_takes_what_else_is_there(self):
        receipt_b = self.receive_into(self.order_b, "10", self.warehouse, lot=self.lot_b)
        receipt_b.create_return(quantities={receipt_b.lines.get(): Decimal("4")}, debit_bills=False)
        self.order_a.lines.update(quantity=Decimal("14"))
        self.receive_into(self.order_a, "14", self.warehouse, lot=self.lot_a)
        # A's own ten of F-1, then four of F-2 put back from B's return.
        self.assertEqual(self.at_sub(), (Decimal("0"), Decimal("0")))


class HeldAssembliesAcceptedAreAConcessionTests(_SentBatches):
    """
    Follow-up to O135: ten assemblies kept by no batch, made from F-1 and
    received into the bay, cleared to the store. That is a concession on a
    rejected batch: a named decider, a reason, and not whoever received them.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        self.bay = Warehouse.objects.create(code="QA", name="Inspection bay", is_quarantine=True)
        self.receiver = User.objects.create(username="stores_qm")
        self.manager = User.objects.create(username="qm")
        for user, roles in ((self.receiver, ("Warehouse Staff", "Quality Manager")),
                            (self.manager, ("Quality Manager",))):
            user.groups.add(*Group.objects.filter(name__in=roles))

    def received(self):
        _sent, order = self.sent_then_rejected()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 2, 1),
                                              created_by=self.receiver)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.first(), warehouse=self.bay,
                                        quantity_received=Decimal("10"), created_by=self.receiver)
        receipt.post()
        return receipt

    def accept(self, user, receipt, **data):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user)
        return client.post(f"/api/purchasing/goods-receipts/{receipt.pk}/accept/",
                           {"warehouse": self.warehouse.pk, **data}, format="json")

    def test_without_a_reason_it_is_refused(self):
        receipt = self.received()
        response = self.accept(self.manager, receipt)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("is a concession", response.content.decode())
        self.assertEqual(self.assembly.on_hand_at(self.bay), Decimal("10"))

    def test_whoever_received_them_does_not_decide_it(self):
        receipt = self.received()
        response = self.accept(self.receiver, receipt, reason="Frames re-checked at the vendor")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("somebody else approves it", response.content.decode())
        self.assertEqual(self.assembly.on_hand_at(self.bay), Decimal("10"))

    def test_another_with_a_reason_clears_them_and_is_named(self):
        from .models import ReceiptInspection

        receipt = self.received()
        response = self.accept(self.manager, receipt, reason="Frames re-checked at the vendor")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((self.assembly.on_hand_at(self.bay), self.assembly.on_hand_at(self.warehouse)),
                         (Decimal("0"), Decimal("10")))
        decision = ReceiptInspection.objects.get(receipt_line__receipt=receipt)
        self.assertEqual((decision.decided_by, decision.note), (self.manager, "Frames re-checked at the vendor"))

    def test_a_quality_inspector_does_not_take_the_concession(self):
        # Decision 22: a decision on a batch quality rejected is the Quality
        # Manager's, not every holder of the receipt inspection's right.
        from django.contrib.auth.models import Group, User

        inspector = User.objects.create(username="inspector")
        inspector.groups.add(Group.objects.get(name="Quality Inspector"))
        receipt = self.received()
        response = self.accept(inspector, receipt, reason="Frames re-checked at the vendor")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("is the Quality Manager's decision", response.content.decode())
        self.assertEqual(self.assembly.on_hand_at(self.bay), Decimal("10"))

    def test_the_quality_manager_is_told_and_the_to_do_closes_with_the_concession(self):
        from apps.core.chatter import FollowUp

        receipt = self.received()
        self.assertEqual(receipt.quality_told.told, ["qm"])  # the receiver is a Quality Manager too, barred
        (to_do,) = FollowUp.objects.filter(object_id=receipt.pk, link=f"/purchasing/goods-in/{receipt.pk}")
        self.assertEqual((to_do.assigned_to, to_do.done_on), (self.manager, None))
        self.assertEqual(self.accept(self.manager, receipt, reason="Frames re-checked at the vendor").status_code,
                         200)
        to_do.refresh_from_db()
        self.assertEqual(to_do.done_by, self.manager)

    def test_a_reason_longer_than_its_field_is_refused_not_cut(self):
        receipt = self.received()
        response = self.accept(self.manager, receipt, reason="x" * 256)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("255 characters or fewer; this is 256", response.content.decode())
        self.assertEqual(self.assembly.on_hand_at(self.bay), Decimal("10"))

    def test_posting_over_the_api_says_who_was_told(self):
        from rest_framework.test import APIClient

        _sent, order = self.sent_then_rejected()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 2, 1),
                                              created_by=self.receiver)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.first(), warehouse=self.bay,
                                        quantity_received=Decimal("10"), created_by=self.receiver)
        client = APIClient()
        client.force_authenticate(self.receiver)
        response = client.post(f"/api/purchasing/goods-receipts/{receipt.pk}/post_receipt/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["quality_told"]["told"], ["qm"])
        self.assertEqual(len(response.json()["held"]), 1)

    def test_goods_held_for_nothing_but_inspection_clear_as_before(self):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=datetime.date(2026, 1, 1))
        PurchaseOrderLine.objects.create(order=order, item=self.motor, uom=self.uom,
                                         quantity=Decimal("5"), unit_price=Decimal("12"))
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 2, 1),
                                              created_by=self.receiver)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.first(), warehouse=self.bay,
                                        quantity_received=Decimal("5"), created_by=self.receiver)
        receipt.post()
        response = self.accept(self.receiver, receipt)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.motor.on_hand_at(self.bay), Decimal("0"))


class HeldAssembliesKeepALotOfTheirOwnTests(_SentBatches):
    """
    Review F1: F-1 passed 1 January and went for 10 assemblies; 5 came back
    into A-1, which passed its own inspection on 15 January; F-1 was
    rejected on 2 February; the other 5, into A-1, read as released.
    """

    def test_held_assemblies_are_not_received_into_a_lot_already_inspected(self):
        from apps.quality.models import Characteristic, Inspection, InspectionPlan, PlanLine, Reading

        Item.objects.filter(pk=self.assembly.pk).update(tracking="lot")
        self.assembly.refresh_from_db()
        self.mandatory_plan()
        torque = Characteristic.objects.create(code="TRQ", name="Torque", uom=self.uom)
        own = InspectionPlan.objects.create(item=self.assembly, is_mandatory=True)
        own_line = PlanLine.objects.create(plan=own, characteristic=torque, lower_limit=Decimal("0"),
                                           upper_limit=Decimal("1"))
        sent = self.batch("F-1", "10", datetime.date(2026, 12, 31))
        self.inspect(sent, "0.5", datetime.date(2026, 1, 1))
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        self.receive_into(order, "5", self.warehouse, lot=made)
        inspection = Inspection.objects.create(lot=made, plan=own, inspected_on=datetime.date(2026, 1, 15))
        Reading.objects.create(inspection=inspection, plan_line=own_line, value=Decimal("0.5"))
        inspection.post()
        self.inspect(sent, "3", datetime.date(2026, 2, 2))
        with self.assertRaisesMessage(ValidationError, "receive them into a lot of their own"):
            self.receive_into(order, "5", self.warehouse, lot=made)
        self.assertEqual((made.on_hand_at(self.warehouse), sent.on_hand_at(self.subcontractor)),
                         (Decimal("5"), Decimal("5")))
        apart = self.lot_model.objects.create(item=self.assembly, code="A-2")
        receipt = self.receive_into(order, "5", self.warehouse, lot=apart)
        self.assertEqual(len(receipt.held_assemblies()), 1)


class AssembliesFromAHeldBatchPassedWaitTests(_SentBatches):
    """
    Review F3: ten assemblies of A-1 made from F-1, rejected; an ordinary
    pass of A-1 by anyone but the Quality Manager released them with no
    reason. It waits for the Quality Manager, as their concession would.
    """

    def setUp(self):
        super().setUp()
        from apps.quality.models import Characteristic, InspectionPlan, PlanLine

        Item.objects.filter(pk=self.assembly.pk).update(tracking="lot")
        self.assembly.refresh_from_db()
        _sent, order = self.sent_then_rejected()
        self.made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        self.receive_into(order, "10", self.warehouse, lot=self.made)
        torque = Characteristic.objects.create(code="TRQ", name="Torque", uom=self.uom)
        self.own = InspectionPlan.objects.create(item=self.assembly, is_mandatory=True)
        self.own_line = PlanLine.objects.create(plan=self.own, characteristic=torque,
                                                lower_limit=Decimal("0"), upper_limit=Decimal("1"))

    def passed(self, by=None):
        from apps.quality.models import Inspection, Reading

        inspection = Inspection.objects.create(lot=self.made, plan=self.own, inspected_on=datetime.date(2026, 2, 5))
        Reading.objects.create(inspection=inspection, plan_line=self.own_line, value=Decimal("0.5"))
        return inspection.post(by=by)

    def test_a_pass_by_anyone_else_waits_and_the_lot_stays_held(self):
        from apps.quality.models import Disposition
        from apps.quality.release import why_held

        inspection = self.passed()
        self.assertEqual(inspection.disposition, Disposition.AWAITING)
        self.assertIn("passed", inspection.told)
        self.assertIn("A-1 is held: it passed", why_held(self.assembly, self.made, warehouse=self.warehouse))
        self.assertIn("made from FRM / F-1, which quality holds", why_held(self.assembly, self.made))

    def test_the_quality_manager_posting_it_releases_it(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command

        from apps.quality.release import why_held

        call_command("setup_roles", verbosity=0)
        manager = User.objects.create(username="qm")
        manager.groups.add(Group.objects.get(name="Quality Manager"))
        self.passed(by=manager)
        self.assertEqual(why_held(self.assembly, self.made, warehouse=self.warehouse), "")


class WhoeverPostedTheReceiptReceivedItTests(HeldAssembliesAcceptedAreAConcessionTests):
    """
    Review F2: a clerk keyed the receipt; the Warehouse Staff and Quality
    Manager who posted it was told to decide it and could concede it.
    """

    def posted_by_the_receiver(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        clerk = User.objects.create(username="clerk")
        _sent, order = self.sent_then_rejected()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 2, 1),
                                              created_by=clerk)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.first(), warehouse=self.bay,
                                        quantity_received=Decimal("10"), created_by=clerk)
        client = APIClient()
        client.force_authenticate(self.receiver)
        response = client.post(f"/api/purchasing/goods-receipts/{receipt.pk}/post_receipt/")
        self.assertEqual(response.status_code, 200, response.content)
        return receipt, response.json()["quality_told"]

    def test_the_poster_is_not_told_to_decide_it(self):
        _receipt, told = self.posted_by_the_receiver()
        self.assertEqual(told["told"], ["qm"])

    def test_the_poster_does_not_concede_it(self):
        receipt, _told = self.posted_by_the_receiver()
        response = self.accept(self.receiver, receipt, reason="I received them and they are fine")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("somebody else approves it", response.content.decode())


class HeldAssembliesSayWhatIsStillHeldTests(_SentBatches):
    """Review F5 and F7: the receipt lists what still waits, at what still waits, and asks only subcontract lines."""

    def test_a_tracked_lot_sent_back_in_full_is_not_listed(self):
        Item.objects.filter(pk=self.assembly.pk).update(tracking="lot")
        self.assembly.refresh_from_db()
        _sent, order = self.sent_then_rejected()
        made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        receipt = self.receive_into(order, "10", self.warehouse, lot=made)
        receipt.create_return(quantities={receipt.lines.get(): Decimal("4")}, debit_bills=False)
        (row,) = receipt.held_assemblies()
        self.assertEqual(row["quantity"], Decimal("6"))
        receipt.create_return(debit_bills=False)
        self.assertEqual(receipt.held_assemblies(), [])

    def test_untracked_four_of_ten_conceded_leaves_six_listed(self):
        from django.contrib.auth.models import User

        bay = Warehouse.objects.create(code="QA", name="Inspection bay", is_quarantine=True)
        _sent, order = self.sent_then_rejected()
        receipt = self.receive_into(order, "10", bay)
        boss = User.objects.create(username="boss", is_superuser=True)
        receipt.accept(self.warehouse, quantities={receipt.lines.get(): Decimal("4")}, by=boss,
                       reason="Four re-checked at the vendor")
        (row,) = receipt.held_assemblies()
        self.assertEqual(row["quantity"], Decimal("6"))

    def test_a_plain_receipt_asks_once(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=datetime.date(2026, 1, 1))
        for index in range(15):
            item = Item.objects.create(sku=f"X{index}", name=f"X{index}", uom=self.uom)
            PurchaseOrderLine.objects.create(order=order, item=item, uom=self.uom, quantity=Decimal("1"),
                                             unit_price=Decimal("2"))
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 2, 1))
        for line in order.lines.all():
            GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, warehouse=self.warehouse,
                                            quantity_received=Decimal("1"))
        receipt.post()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(receipt.held_assemblies(), [])
        self.assertEqual(len(queries), 1)


class TheQualityManagersToDoClosesWhenNothingIsHeldTests(_SentBatches):
    """Review F4: ten assemblies of A-1 from rejected F-1, the Quality Manager told; each way out closes the to-do."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        self.manager = User.objects.create(username="qm")
        self.manager.groups.add(Group.objects.get(name="Quality Manager"))
        Item.objects.filter(pk=self.assembly.pk).update(tracking="lot")
        self.assembly.refresh_from_db()
        self.sent, order = self.sent_then_rejected()
        self.made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        self.receipt = self.receive_into(order, "10", self.warehouse, lot=self.made)
        self.assertEqual(self.open(), 1)

    def open(self):
        from apps.core.chatter import FollowUp

        return FollowUp.objects.filter(object_id=self.receipt.pk, done_on__isnull=True,
                                       content_type__model="goodsreceipt").count()

    def test_sent_back_in_full_it_closes_and_in_part_it_does_not(self):
        self.receipt.create_return(quantities={self.receipt.lines.get(): Decimal("4")}, debit_bills=False)
        self.assertEqual(self.open(), 1)
        self.receipt.create_return(debit_bills=False)
        self.assertEqual(self.open(), 0)

    def test_the_source_batch_passed_again_closes_it(self):
        self.inspect(self.sent, "0.5", datetime.date(2026, 2, 3))
        self.assertEqual((self.receipt.held_assemblies(), self.open()), ([], 0))

    def test_the_assembly_lot_released_by_the_quality_manager_closes_it(self):
        from apps.quality.models import Characteristic, Inspection, InspectionPlan, PlanLine, Reading

        torque = Characteristic.objects.create(code="TRQ", name="Torque", uom=self.uom)
        own = InspectionPlan.objects.create(item=self.assembly, is_mandatory=True)
        line = PlanLine.objects.create(plan=own, characteristic=torque, lower_limit=Decimal("0"),
                                       upper_limit=Decimal("1"))
        inspection = Inspection.objects.create(lot=self.made, plan=own, inspected_on=datetime.date(2026, 2, 5))
        Reading.objects.create(inspection=inspection, plan_line=line, value=Decimal("0.5"))
        inspection.post(by=self.manager)
        self.assertEqual((self.receipt.held_assemblies(), self.open()), ([], 0))
