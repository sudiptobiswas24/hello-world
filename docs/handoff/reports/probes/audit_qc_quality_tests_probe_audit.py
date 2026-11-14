"""
Audit probes, 9 October: quality where manufacturing, stores and sales
call it. Each test states one claim about stock or state and fails with
the observed figure beside the expected one. Expected figures were
worked in scratchpad/calc/expected.py before they were typed here.
"""

import datetime
import unittest
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TransactionTestCase, tag
from django.utils import timezone

from apps.inventory.models import Item, Lot, MovementType, StockMovement, Warehouse
from apps.inventory.transfers import StockTransfer, StockTransferLine
from apps.manufacturing.orders import MaterialIssue, MaterialIssueLine, WorkOrder
from apps.manufacturing.tape_loads import CreelSide, TapeLoad
from apps.manufacturing.tests_orders import TODAY, RunTestCase
from apps.manufacturing.tests_station import at
from apps.manufacturing.tests_tape_loads import TapeLoadTestCase
from apps.quality.models import (
    Characteristic,
    Disposition,
    Inspection,
    InspectionPlan,
    PlanLine,
    Reading,
)
from apps.quality.release import plan_for, release_status


class _HeldDrum(RunTestCase):
    """PP-A, 3,000 kg of a polymer whose plan is mandatory: denier 950-1050."""

    def setUp(self):
        super().setUp()
        self.virgin.tracking = "lot"
        self.virgin.save()
        self.drum = Lot.objects.create(item=self.virgin, code="PP-A")
        self.stock(self.virgin, "3000", "100", lot=self.drum)
        self.denier = Characteristic.objects.create(code="DEN", name="Denier", uom=self.kg)
        self.plan = InspectionPlan.objects.create(item=self.virgin, is_mandatory=True)
        self.line = PlanLine.objects.create(
            plan=self.plan, characteristic=self.denier, target=Decimal("1000"),
            lower_limit=Decimal("950"), upper_limit=Decimal("1050"), sample_size=1,
        )

    def inspect(self, value, on=TODAY, **kwargs):
        inspection = Inspection.objects.create(
            lot=self.drum, plan=self.plan, inspected_on=on, **kwargs
        )
        Reading.objects.create(inspection=inspection, plan_line=self.line,
                               value=Decimal(str(value)))
        inspection.post()
        return inspection

    def issue_drum(self, quantity="100"):
        run = WorkOrder.objects.create(
            item=self.tape, bom=self.bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, work_centre=self.loom,
        )
        run.release(TODAY)
        document = MaterialIssue.objects.create(work_order=run, issue_date=TODAY,
                                                warehouse=self.plant)
        MaterialIssueLine.objects.create(issue=document, item=self.virgin,
                                         quantity=Decimal(quantity), uom=self.kg,
                                         lot=self.drum, line_number=1)
        return document


class QuarantineProbe(RunTestCase):
    def test_probe_issue_from_quarantine_is_refused(self):
        """
        Delivery (sales/models.py:3950) and transfer (inventory/transfers.py:231)
        refuse a quarantine warehouse. A material issue is the third way out.
        """
        hold = Warehouse.objects.create(code="QC", name="Quality hold", is_quarantine=True)
        StockMovement.objects.create(
            item=self.filler, warehouse=hold, movement_type=MovementType.RECEIPT,
            uom=self.kg, quantity=Decimal("100"), unit_cost=Decimal("30"),
            occurred_at=timezone.now(),
        )
        run = self.order("1000")
        run.release(TODAY)
        document = MaterialIssue.objects.create(work_order=run, issue_date=TODAY, warehouse=hold)
        MaterialIssueLine.objects.create(issue=document, item=self.filler,
                                         quantity=Decimal("40"), uom=self.kg, line_number=1)
        try:
            document.post()
        except ValidationError:
            return
        self.fail(
            "Issued 40 kg out of quarantine into a run: QC holds "
            f"{self.filler.on_hand_at(hold)} kg, expected the issue refused and 100 kg kept."
        )


class RetiredPlanProbe(_HeldDrum):
    def test_probe_rejected_lot_stays_held_when_its_plan_is_retired(self):
        self.inspect(1200)
        self.assertEqual(release_status(self.drum), "held")
        self.plan.is_active = False
        self.plan.save()
        try:
            self.issue_drum().post()
        except ValidationError:
            return
        self.fail(
            f"Rejected PP-A went into a run once its plan was retired: release_status says "
            f"{release_status(self.drum)!r}, drum on hand {self.drum.on_hand_at(self.plant)} kg; "
            "expected refused and 3000 kg kept."
        )

    def test_probe_rejected_lot_stays_held_when_its_plan_is_made_advisory(self):
        self.inspect(1200)
        InspectionPlan.objects.filter(pk=self.plan.pk).update(is_mandatory=False)
        try:
            self.issue_drum().post()
        except ValidationError:
            return
        self.fail(
            f"Rejected PP-A went into a run once its plan was made advisory: release_status "
            f"{release_status(self.drum)!r}, drum on hand {self.drum.on_hand_at(self.plant)} kg; "
            "expected refused and 3000 kg kept."
        )


class ReinspectionProbe(_HeldDrum):
    def test_probe_a_rejection_posted_after_a_pass_holds_the_lot(self):
        """
        Passed on 1 June; a retest of the retained sample from 30 May is
        posted afterwards and rejects. Either it is refused, or it holds.
        """
        self.inspect(1000)
        try:
            later = self.inspect(1200, on=TODAY - datetime.timedelta(days=2))
        except ValidationError:
            return
        self.assertEqual(later.disposition, Disposition.REJECT)
        self.assertEqual(
            release_status(self.drum), "held",
            f"{later.number} rejected PP-A and stands, yet the lot reads "
            f"{release_status(self.drum)!r}: the earlier-dated verdict is outranked "
            "without a word. Two verdicts stand.",
        )


class TransferProbe(_HeldDrum):
    def test_probe_a_rejected_lot_is_not_transferred(self):
        self.inspect(1200)
        store = Warehouse.objects.create(code="W2", name="Second store")
        transfer = StockTransfer.objects.create(from_warehouse=self.plant, to_warehouse=store,
                                                transfer_date=TODAY)
        StockTransferLine.objects.create(transfer=transfer, item=self.virgin, uom=self.kg,
                                         lot=self.drum, quantity=Decimal("100"))
        try:
            transfer.post()
        except ValidationError:
            return
        self.fail(
            f"Rejected PP-A transferred: W2 holds {self.drum.on_hand_at(store)} kg, plant "
            f"{self.drum.on_hand_at(self.plant)} kg; expected refused (plant 3000)."
        )


class TapeLoadProbe(TapeLoadTestCase):
    """D-1 rejected under a mandatory plan on the tape."""

    def setUp(self):
        super().setUp()
        InspectionPlan.objects.filter(item=self.tape).update(is_active=False)
        denier = Characteristic.objects.create(code="DENP", name="Denier probe", uom=self.kg)
        self.plan = InspectionPlan.objects.create(item=self.tape, is_mandatory=True)
        line = PlanLine.objects.create(plan=self.plan, characteristic=denier,
                                       lower_limit=Decimal("950"), upper_limit=Decimal("1050"),
                                       sample_size=1)
        inspection = Inspection.objects.create(lot=self.doffs["D-1"], plan=self.plan,
                                               inspected_on=TODAY)
        Reading.objects.create(inspection=inspection, plan_line=line, value=Decimal("1200"))
        inspection.post()
        assert release_status(self.doffs["D-1"]) == "held"

    def test_control_a_held_doff_is_refused_on_a_run_that_issues(self):
        with self.assertRaisesMessage(ValidationError, "is held"):
            self.load("D-1", "30", CreelSide.WARP, 9)

    def test_probe_a_held_doff_is_refused_on_a_backflushed_run(self):
        WorkOrder.objects.filter(pk=self.run.pk).update(backflush=True)
        self.run.refresh_from_db()
        try:
            self.load("D-1", "30", CreelSide.WARP, 9)
        except ValidationError:
            return
        loads = TapeLoad.objects.filter(lot=self.doffs["D-1"], voided_at__isnull=True)
        try:
            self.load("D-1", "70", CreelSide.WEFT, 9, 5)
            self.weigh(gross="52", declared="475", when=at(TODAY, 10, 42))
            roll = "booked"
        except ValidationError as exc:
            roll = f"refused: {exc.messages[0][:140]}"
        self.fail(
            f"Held D-1 loaded on a backflushed loom: {loads.count()} loads, "
            f"{sum(load.kg for load in loads)} kg, stand; expected refused at the creel. The roll woven "
            f"from it is then {roll}."
        )


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class ComplaintRaceProbe(TransactionTestCase):
    def test_probe_no_action_lands_on_a_complaint_as_it_closes(self):
        from apps.e2e.tests_races import fixture, race
        from apps.manufacturing.complaints import (
            ActionKind,
            Complaint,
            ComplaintStatus,
            CorrectiveAction,
        )
        from apps.manufacturing.tests_complaints import ComplaintTestCase

        made = fixture(self, ComplaintTestCase)
        complaint = made.ready()
        outcomes = race(
            (Complaint, CorrectiveAction),
            lambda: Complaint.objects.get(pk=complaint.pk).close(
                "Metal in regrind", by=made.qa, on_date=TODAY + datetime.timedelta(days=1)),
            lambda: CorrectiveAction.objects.create(
                complaint_id=complaint.pk, kind=ActionKind.PREVENTIVE,
                description="Audit the regrind supplier", owner=made.plant_head,
                due_on=TODAY + datetime.timedelta(days=30)),
        )
        complaint.refresh_from_db()
        undone = CorrectiveAction.objects.filter(complaint=complaint, done_on__isnull=True).count()
        self.assertFalse(
            complaint.status == ComplaintStatus.CLOSED and undone,
            f"{outcomes}: {complaint} is {complaint.status} with {undone} action not done; "
            "expected one of the two refused.",
        )


from apps.gst.tests import DAY, GstReturnTestCase, gstin  # noqa: E402


class ComplaintSettledThenRejectedProbe(GstReturnTestCase):
    def test_probe_a_settled_complaint_is_not_rejected(self):
        """settle() refuses a rejected complaint; its mirror, reject() of a settled one."""
        from apps.manufacturing.complaints import Complaint
        from apps.manufacturing.tests_complaints import person

        buyer = self.party("CEMENT", gstin=gstin("27AABCC5555E1Z"))
        invoice = self.sell(buyer, "10000")
        complaint = Complaint.objects.create(customer=buyer, received_on=DAY, category="seam",
                                             description="Seams opened in the silo")
        complaint.settle(invoice, Decimal("500"), "quality")
        try:
            complaint.reject("Not our sacks after all", by=person("EMP-0601", "Quality head"))
        except ValidationError:
            return
        complaint.refresh_from_db()
        self.fail(
            f"{complaint} is {complaint.status} and still says it cost {complaint.cost()}; "
            "expected the rejection refused while a settlement stands (cost 500.00 on an open "
            "or closed complaint)."
        )


from apps.purchasing.tests_subcontract import SubcontractTestCase  # noqa: E402


class BatchKeptComponentsToASubcontractorProbe(SubcontractTestCase):
    def test_probe_a_batch_kept_component_can_be_sent(self):
        """
        Anything a mandatory plan covers is kept by batch (InspectionPlan.save),
        so this is the only kind of component quality can hold.
        """
        Item.objects.filter(pk=self.frame.pk).update(tracking="lot")
        self.frame.refresh_from_db()
        StockMovement.objects.filter(item=self.frame).delete()
        batch = Lot.objects.create(item=self.frame, code="F-1")
        StockMovement.objects.create(item=self.frame, warehouse=self.warehouse,
                                     movement_type=MovementType.RECEIPT, uom=self.frame.uom,
                                     quantity=Decimal("100"), unit_cost=Decimal("8"), lot=batch,
                                     occurred_at=timezone.now())
        order = self.subcontract_order("10")
        try:
            order.issue_components(self.warehouse)
        except ValidationError as exc:
            self.fail(f"issue_components refused: {exc.messages[0]} Expected 10 of F-1 at "
                      f"the subcontractor; it holds {batch.on_hand_at(self.subcontractor)}.")
        self.assertEqual(batch.on_hand_at(self.subcontractor), Decimal("10"))


from apps.manufacturing.tests_bag_counts import ConversionTestCase  # noqa: E402


class BagCountVoidProbe(ConversionTestCase):
    def test_probe_a_count_whose_inspection_was_voided_can_still_be_withdrawn(self):
        """void_gauged (station_gauge.py:108) skips an inspection already voided; void_bags does not."""
        from apps.manufacturing.conversion import void_bags

        count = self.count()
        lot = count.inspection.lot
        count.inspection.void("Scale read wrong; weighing again")
        try:
            void_bags(count, self.supervisor, "Miscounted")
        except ValidationError as exc:
            self.fail(
                f"void_bags refused: {exc.messages[0]} The count stands and {lot.code} still "
                f"holds {lot.on_hand_at(self.plant)} bags; expected withdrawn and 0."
            )
        self.assertEqual(lot.on_hand_at(self.plant), Decimal("0"))
