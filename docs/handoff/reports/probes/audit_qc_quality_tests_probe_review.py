"""
Review probes, 10 October: the fixes for O110-O122 (3c06d1f..418540d).
Expected figures worked in scratchpad/calc/review.py.
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
from apps.manufacturing.tests_orders import TODAY
from apps.purchasing.tests_subcontract import SubcontractTestCase
from apps.quality.models import (
    Characteristic,
    Disposition,
    Inspection,
    InspectionPlan,
    PlanLine,
    Reading,
)
from apps.quality.release import release_status

from .tests_probe_audit import _HeldDrum


class SameDayReinspectionProbe(_HeldDrum):
    def test_probe_an_older_draft_posted_after_a_pass_on_the_same_day_holds(self):
        """O113 refuses an earlier date; the same date is ranked by id, i.e. by when drafted."""
        rejecting = Inspection.objects.create(lot=self.drum, plan=self.plan, inspected_on=TODAY)
        Reading.objects.create(inspection=rejecting, plan_line=self.line, value=Decimal("1200"))
        self.inspect(1000)  # drafted after, posted first: passes
        try:
            rejecting.post()
        except ValidationError:
            return
        self.assertEqual(rejecting.disposition, Disposition.REJECT)
        self.assertEqual(
            release_status(self.drum), "held",
            f"{rejecting.number} rejected PP-A on {TODAY}, posted last, and stands; the lot reads "
            f"{release_status(self.drum)!r} (the pass has the higher id). Expected held or refused.",
        )


class SplitBackdatedProbe(_HeldDrum):
    def test_probe_a_split_of_a_rejected_lot_is_not_passed_by_a_verdict_dated_before_the_rejection(self):
        from apps.manufacturing.rebatch import rebatch

        self.inspect(1200)  # PP-A rejected on 1 June
        part = Lot.objects.create(item=self.virgin, code="PP-A2")
        rest = Lot.objects.create(item=self.virgin, code="PP-A3")
        rebatch(self.virgin, self.plant, [(self.drum, "100")], [(part, "60"), (rest, "40")], "split",
                on_date=TODAY)
        self.assertEqual(release_status(part), "held")
        early = Inspection.objects.create(lot=part, plan=self.plan,
                                          inspected_on=TODAY - datetime.timedelta(days=7))
        Reading.objects.create(inspection=early, plan_line=self.line, value=Decimal("1000"))
        try:
            early.post()
        except ValidationError:
            return
        self.fail(
            f"{early.number} passed PP-A2, dated {early.inspected_on}: before PP-A's rejection of "
            f"{TODAY} and before the split made PP-A2. PP-A2 reads {release_status(part)!r}; "
            "expected refused, as a re-inspection of PP-A dated so would be (O113)."
        )

    def test_control_a_transfer_then_an_issue_from_the_new_place_is_refused(self):
        self.inspect(1200)
        store = Warehouse.objects.create(code="W2", name="Second store")
        transfer = StockTransfer.objects.create(from_warehouse=self.plant, to_warehouse=store,
                                                transfer_date=TODAY)
        StockTransferLine.objects.create(transfer=transfer, item=self.virgin, uom=self.kg,
                                         lot=self.drum, quantity=Decimal("100"))
        transfer.post()
        document = self.issue_drum()
        document.warehouse = store
        document.save()
        with self.assertRaisesMessage(ValidationError, "is held"):
            document.post()


class AdvisoryFailureProbe(_HeldDrum):
    def test_evidence_a_failed_measurement_under_an_advisory_plan_now_holds(self):
        InspectionPlan.objects.filter(pk=self.plan.pk).update(is_mandatory=False)
        inspection = self.inspect(1200)  # nobody chose a disposition
        self.assertEqual(inspection.disposition, Disposition.REJECT)
        with self.assertRaisesMessage(ValidationError, "is held"):
            self.issue_drum().post()


class _Batches(SubcontractTestCase):
    def setUp(self):
        super().setUp()
        Item.objects.filter(pk=self.frame.pk).update(tracking="lot")
        self.frame.refresh_from_db()
        StockMovement.objects.filter(item=self.frame).delete()

    def batch(self, code, quantity, expires=None, warehouse=None):
        made = Lot.objects.create(item=self.frame, code=code, expires_on=expires)
        StockMovement.objects.create(item=self.frame, warehouse=warehouse or self.warehouse,
                                     movement_type=MovementType.RECEIPT, uom=self.frame.uom,
                                     quantity=Decimal(quantity), unit_cost=Decimal("8"), lot=made,
                                     occurred_at=timezone.now())
        return made


class SubcontractQuarantineProbe(_Batches):
    def test_probe_components_are_not_sent_from_quarantine(self):
        bay = Warehouse.objects.create(code="QA", name="Inspection bay", is_quarantine=True)
        self.batch("F-Q", "100", warehouse=bay)
        self.stock(self.motor, "100", "12", warehouse=bay)
        order = self.subcontract_order("10")
        try:
            order.issue_components(bay)
        except ValidationError as exc:
            print("QUARANTINE REFUSAL:", exc.messages)
            return
        self.fail(f"Ten frames went to the subcontractor out of the quarantine bay: QA holds "
                  f"{self.frame.on_hand_at(bay)}, SUB {self.frame.on_hand_at(self.subcontractor)}; "
                  "expected refused (QA 100).")


class SubcontractHeldAfterSendingProbe(_Batches):
    def test_probe_assemblies_are_received_when_their_sent_batch_is_held_since(self):
        sent = self.batch("F-1", "100")
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        check = Characteristic.objects.create(code="FLAT", name="Flatness", uom=self.uom)
        plan = InspectionPlan.objects.create(item=self.frame, is_mandatory=False)
        line = PlanLine.objects.create(plan=plan, characteristic=check, lower_limit=Decimal("0"),
                                       upper_limit=Decimal("1"))
        inspection = Inspection.objects.create(lot=sent, plan=plan, inspected_on=datetime.date(2026, 1, 2))
        Reading.objects.create(inspection=inspection, plan_line=line, value=Decimal("3"))
        inspection.post()
        try:
            self.receive(order, "10")
        except ValidationError as exc:
            self.fail(f"The ten assemblies made before F-1 was rejected cannot be received: "
                      f"{exc.messages[0]} F-1 at SUB: {sent.on_hand_at(self.subcontractor)}. "
                      "Expected consumed as sent (expiry is no bar there either).")


class SubcontractReturnByBatchProbe(_Batches):
    def test_probe_returns_one_at_a_time_put_each_batch_back_what_it_gave(self):
        first = self.batch("F-1", "1", datetime.date(2026, 12, 31))
        second = self.batch("F-2", "99", datetime.date(2027, 6, 30))
        order = self.subcontract_order("3")
        order.issue_components(self.warehouse)
        receipt = self.receive(order, "3")
        for _ in range(3):
            receipt.create_return(quantities={receipt.lines.get(): Decimal("1")}, debit_bills=False)
        got = (first.on_hand_at(self.subcontractor), second.on_hand_at(self.subcontractor))
        self.assertEqual(
            got, (Decimal("1"), Decimal("2")),
            f"F-1 gave 1 frame and F-2 gave 2 to three assemblies; returned one at a time they "
            f"hold {got} at the subcontractor.",
        )


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class ComplaintLotRaceProbe(TransactionTestCase):
    def test_probe_no_batch_is_named_on_a_complaint_as_it_is_rejected(self):
        from apps.e2e.tests_races import fixture, race
        from apps.manufacturing.complaints import Complaint, ComplaintLot, ComplaintStatus
        from apps.manufacturing.tests_complaints import ComplaintTestCase

        made = fixture(self, ComplaintTestCase)
        complaint = made.complaint()
        outcomes = race(
            (Complaint, ComplaintLot),
            lambda: Complaint.objects.get(pk=complaint.pk).reject("Not our sacks", by=made.qa),
            lambda: Complaint.objects.get(pk=complaint.pk).add_lot(made.tape_lot, "600"),
        )
        complaint.refresh_from_db()
        named = complaint.complained_lots.count()
        self.assertFalse(
            complaint.status == ComplaintStatus.REJECTED and named,
            f"{outcomes}: {complaint} is {complaint.status} and names {named} batch added at the "
            "same moment; sequentially the batch is refused ('reopen it to change it').",
        )
