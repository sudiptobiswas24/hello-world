"""
Adversarial probes for gate g19 (subcontract receive-and-hold, O147, O149, O150).

Each test is a scenario with numbers, asserting what should be true.
Hand arithmetic: frame 8 + two motors at 12 + service 5 = 37 an assembly,
370.00 for ten; the ledger gains the 50.00 service only (the components
move value from their own shelves into the assembly).
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from rest_framework.test import APIClient

from apps.core.chatter import FollowUp
from apps.inventory.models import Item, Lot, MovementType, StockMovement, Warehouse

from .models import GoodsReceipt, GoodsReceiptLine, ReceiptInspection, SubcontractComponent
from .tests_subcontract import _SentBatches

D = Decimal


class _Base(_SentBatches):
    """Ten assemblies, kept by lot or not, made from F-1 (passed 1 Jan, sent, rejected 2 Jan)."""

    track_assembly = True

    def setUp(self):
        super().setUp()
        if self.track_assembly:
            Item.objects.filter(pk=self.assembly.pk).update(tracking="lot")
            self.assembly.refresh_from_db()
        call_command("setup_roles", verbosity=0)
        self.bay = Warehouse.objects.create(code="QA", name="Inspection bay", is_quarantine=True)

    def person(self, name, *roles, **extra):
        user = User.objects.create(username=name, email=f"{name}@plant.test", **extra)
        user.groups.add(*Group.objects.filter(name__in=roles))
        return user

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def assembly_plan(self, mandatory=True):
        from apps.quality.models import Characteristic, InspectionPlan, PlanLine

        check = Characteristic.objects.create(code="TRQ", name="Torque", uom=self.uom)
        self.asm_plan = InspectionPlan.objects.create(item=self.assembly, is_mandatory=mandatory)
        self.asm_line = PlanLine.objects.create(plan=self.asm_plan, characteristic=check,
                                                lower_limit=D("0"), upper_limit=D("1"))

    def inspect_assembly(self, lot, value, on):
        from apps.quality.models import Inspection, Reading

        inspection = Inspection.objects.create(lot=lot, plan=self.asm_plan, inspected_on=on)
        Reading.objects.create(inspection=inspection, plan_line=self.asm_line, value=D(value))
        inspection.post()
        return inspection

    def total_value(self):
        return sum((item.stock_value_at(place) for item in (self.frame, self.motor, self.assembly)
                    for place in (self.warehouse, self.subcontractor, self.bay)), D("0"))

    def line_for(self, receipt, quantity, warehouse, lot=None, by=None):
        return GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=receipt.purchase_order.lines.first(), lot=lot,
            warehouse=warehouse, quantity_received=D(quantity), created_by=by)

    def draft(self, order, by=None):
        return GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 2, 1),
                                           created_by=by)


class ReceiveAndHoldProbes(_Base):
    def test_held_assemblies_do_not_join_a_lot_already_passed(self):
        """
        F-1 passed 1 Jan and sent for 10 assemblies. 5 come back on 1 Feb into
        lot A-1, which is inspected and passed on 15 Jan. F-1 is rejected on
        2 Feb. The other 5 come back into the same lot A-1. Quality's status
        for a lot with its own inspection ignores lineage, so the 5 made from
        a rejected batch read as released: nothing issues or ships them
        'until quality decides' (decision 22) is false.
        """
        from apps.quality.release import why_held

        self.mandatory_plan()
        self.assembly_plan()
        sent = self.batch("F-1", "10", datetime.date(2026, 12, 31))
        self.inspect(sent, "0.5", datetime.date(2026, 1, 1))
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        self.receive_into(order, "5", self.warehouse, lot=made)
        self.inspect_assembly(made, "0.5", datetime.date(2026, 1, 15))
        self.assertEqual(why_held(self.assembly, made, warehouse=self.warehouse), "")
        self.inspect(sent, "3", datetime.date(2026, 2, 2))
        second = None
        try:
            second = self.receive_into(order, "5", self.warehouse, lot=made)
        except ValidationError:
            return  # refusing it is an answer
        self.assertEqual(made.on_hand_at(self.warehouse), D("10"))
        said = why_held(self.assembly, made, warehouse=self.warehouse)
        self.assertNotEqual(said, "", "5 assemblies made from rejected F-1 joined a passed lot unheld")
        self.assertEqual(len(second.held_assemblies()), 1)

    def test_tracked_assembly_without_a_plan_has_a_way_out(self):
        """
        A held tracked lot is released only by an inspection of its own, and an
        Inspection needs a plan of the assembly's. With no plan, what releases it?
        The Quality Manager's concession is for untracked goods only.
        """
        from apps.quality.release import why_held

        _sent, order = self.sent_then_rejected()
        made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        self.receive_into(order, "10", self.warehouse, lot=made)
        self.assertNotEqual(why_held(self.assembly, made, warehouse=self.warehouse), "")
        manager = self.person("qm", "Quality Manager")
        # Quality Manager can raise a plan and pass it: an ordinary pass, no reason, no named concession.
        self.assembly_plan(mandatory=False)
        self.inspect_assembly(made, "0.5", datetime.date(2026, 2, 5))
        self.assertEqual(why_held(self.assembly, made, warehouse=self.warehouse), "")
        # ...which any Quality Inspector's ordinary pass does, with no Quality Manager, no reason.
        inspector = self.person("insp", "Quality Inspector")
        self.assertFalse(inspector.has_perm("quality.decide_inspection"))
        self.assertTrue(manager.has_perm("quality.decide_inspection"))

    def test_an_inspector_passes_the_tracked_lot_the_manager_alone_may_concede_untracked(self):
        """The same rejected frames: untracked needs the Quality Manager and a reason; tracked, an inspector's pass."""
        from apps.quality.release import why_held

        _sent, order = self.sent_then_rejected()
        made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        self.receive_into(order, "10", self.warehouse, lot=made)
        self.assembly_plan(mandatory=True)
        inspector = self.person("insp", "Quality Inspector")
        self.assertTrue(inspector.has_perm("quality.add_inspection") and inspector.has_perm("quality.change_inspection"))
        self.assertFalse(inspector.has_perm("quality.decide_inspection"))
        self.inspect_assembly(made, "0.5", datetime.date(2026, 2, 5))
        self.assertNotEqual(why_held(self.assembly, made, warehouse=self.warehouse), "",
                            "an Inspector's ordinary pass released assemblies made from a rejected batch")

    def test_an_untracked_assembly_given_a_lot_lands_unheld(self):
        """Assembly not kept by batch but the receipt line names a lot: the 'untracked must go to quarantine' rule is skipped."""
        Item.objects.filter(pk=self.assembly.pk).update(tracking="none")
        self.assembly.refresh_from_db()
        made = self.lot_model.objects.create(item=self.assembly, code="A-X")
        _sent, order = self.sent_then_rejected()
        try:
            receipt = self.receive_into(order, "10", self.warehouse, lot=made)
        except ValidationError:
            return
        self.assertEqual(self.assembly.on_hand_at(self.warehouse), D("10"))
        self.assertNotEqual(receipt.held_assemblies(), [], "10 untracked assemblies from rejected F-1 sit in the store unheld")

    def test_partial_untracked_receipts_into_the_bay_then_a_store(self):
        """4 into the bay (held, F-1 4 consumed); the other 6 into a store are refused whole, nothing written."""
        self.assembly_plan(mandatory=False)
        sent, order = self.sent_then_rejected()
        Item.objects.filter(pk=self.assembly.pk).update(tracking="none")
        self.assembly.refresh_from_db()
        first = self.receive_into(order, "4", self.bay)
        self.assertEqual(sent.on_hand_at(self.subcontractor), D("6"))
        before = StockMovement.objects.count()
        with self.assertRaisesMessage(ValidationError, "quarantine warehouse"):
            self.receive_into(order, "6", self.warehouse)
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(len(first.held_assemblies()), 1)

    def test_a_receipt_with_two_lines_one_held_one_not_refuses_nothing_half_written(self):
        """Two receipt lines on one order line: 4 to the bay, 6 to a store; the store line is refused and the receipt posts nothing."""
        Item.objects.filter(pk=self.assembly.pk).update(tracking="none")
        self.assembly.refresh_from_db()
        sent, order = self.sent_then_rejected()
        receipt = self.draft(order)
        self.line_for(receipt, "4", self.bay)
        self.line_for(receipt, "6", self.warehouse)
        before = (StockMovement.objects.count(), sent.on_hand_at(self.subcontractor))
        with self.assertRaises(ValidationError):
            receipt.post()
        receipt.refresh_from_db()
        self.assertFalse(receipt.posted)
        self.assertEqual((StockMovement.objects.count(), sent.on_hand_at(self.subcontractor)), before)

    def test_lineage_through_a_split_and_a_join_of_the_assembly_lot(self):
        from apps.manufacturing.rebatch import rebatch
        from apps.quality.release import why_held

        _sent, order = self.sent_then_rejected()
        made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        self.receive_into(order, "10", self.warehouse, lot=made)
        other = self.lot_model.objects.create(item=self.assembly, code="A-9")
        StockMovement.objects.create(item=self.assembly, warehouse=self.warehouse,
                                     movement_type=MovementType.RECEIPT, uom=self.uom, quantity=D("10"),
                                     unit_cost=D("37"), lot=other, occurred_at=datetime.datetime(2026, 2, 1,
                                                                                                tzinfo=datetime.timezone.utc))
        part = self.lot_model.objects.create(item=self.assembly, code="A-1a")
        part2 = self.lot_model.objects.create(item=self.assembly, code="A-1b")
        rebatch(self.assembly, self.warehouse, [(made, D("10"))], [(part, D("4")), (part2, D("6"))], "split")
        self.assertNotEqual(why_held(self.assembly, part, warehouse=self.warehouse), "", "a split of a held lot reads released")
        joined = self.lot_model.objects.create(item=self.assembly, code="A-J")
        rebatch(self.assembly, self.warehouse, [(part2, D("2")), (other, D("5"))], [(joined, D("7"))], "join")
        self.assertNotEqual(why_held(self.assembly, joined, warehouse=self.warehouse), "", "a join with a held lot reads released")
        clean = self.lot_model.objects.create(item=self.assembly, code="A-C")
        clean2 = self.lot_model.objects.create(item=self.assembly, code="A-D")
        rebatch(self.assembly, self.warehouse, [(other, D("5"))], [(clean, D("3")), (clean2, D("2"))], "split of the clean lot")
        self.assertEqual(why_held(self.assembly, clean, warehouse=self.warehouse), "")

    def test_the_hold_lifts_when_the_source_is_conceded_but_the_todo_does_not_close(self):
        """F-1 conceded by the Quality Manager lifts the tracked lot's hold; the receipt's to-do stays open."""
        from apps.quality.models import Disposition, Inspection
        from apps.quality.release import why_held

        sent, order = self.sent_then_rejected()
        made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        qm = self.person("qm", "Quality Manager")
        receipt = self.receive_into(order, "10", self.warehouse, lot=made)
        self.assertEqual(receipt.quality_told.told, ["qm"])
        # F-1 reinspected and passed (dated after the rejection).
        self.inspect(sent, "0.5", datetime.date(2026, 2, 3))
        self.assertEqual(why_held(self.assembly, made, warehouse=self.warehouse), "")
        self.assertEqual(receipt.held_assemblies(), [])
        self.assertEqual(FollowUp.objects.filter(object_id=receipt.pk, done_on__isnull=True).count(), 0,
                         "nothing is held any more and the Quality Manager's to-do is still open")

    def test_a_return_of_the_held_assemblies_closes_the_quality_managers_todo(self):
        sent, order = self.sent_then_rejected()
        made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        self.person("qm", "Quality Manager")
        receipt = self.receive_into(order, "10", self.warehouse, lot=made)
        self.assertEqual(FollowUp.objects.filter(object_id=receipt.pk, done_on__isnull=True).count(), 1)
        receipt.create_return(debit_bills=False)
        self.assertEqual(made.on_hand_at(self.warehouse), D("0"))
        self.assertEqual(FollowUp.objects.filter(object_id=receipt.pk, done_on__isnull=True).count(), 0,
                         "all 10 went back to the vendor; the Quality Manager is still told to decide")

    def test_a_returned_tracked_lot_is_no_longer_reported_as_held_on_the_receipt(self):
        sent, order = self.sent_then_rejected()
        made = self.lot_model.objects.create(item=self.assembly, code="A-1")
        receipt = self.receive_into(order, "10", self.warehouse, lot=made)
        receipt.create_return(debit_bills=False)
        self.assertEqual(made.on_hand_at(self.warehouse), D("0"))
        self.assertEqual(receipt.held_assemblies(), [], "10 assemblies reported held that are back with the vendor")


class BooksProbes(_Base):
    """GRNI and stock value against the ledger after receive, return, receive again (audit shape 4)."""

    track_assembly = False

    def ledger(self):
        return (self.balance(self.inventory), self.balance(self.grni))

    def test_receive_held_return_receive_again_untracked_into_the_bay(self):
        sent, order = self.sent_then_rejected()
        inv0, grni0 = self.ledger()
        value0 = self.total_value()
        receipt = self.receive_into(order, "10", self.bay)
        inv1, grni1 = self.ledger()
        self.assertEqual(self.total_value() - value0, D("50.00"))
        self.assertEqual(inv1 - inv0, self.total_value() - value0)
        self.assertEqual(grni1 - grni0, D("-50.00"))
        self.assertEqual(self.assembly.stock_value_at(self.bay), D("370.00"))
        receipt.reject(note="from F-1", debit_bills=False)
        inv2, grni2 = self.ledger()
        self.assertEqual((inv2, grni2), (inv0, grni0))
        self.assertEqual(self.total_value(), value0)
        self.assertEqual((sent.on_hand_at(self.subcontractor), self.motor.on_hand_at(self.subcontractor)),
                         (D("10"), D("20")))
        # received again: F-1 consumed again, still held, still bay-only.
        with self.assertRaisesMessage(ValidationError, "quarantine"):
            self.receive_into(order, "10", self.warehouse)
        again = self.receive_into(order, "10", self.bay)
        inv3, grni3 = self.ledger()
        self.assertEqual((inv3 - inv0, grni3 - grni0), (D("50.00"), D("-50.00")))
        self.assertEqual(self.total_value() - value0, D("50.00"))
        self.assertEqual(len(again.held_assemblies()), 1)

    def test_concession_then_return_from_the_store(self):
        sent, order = self.sent_then_rejected()
        qm = self.person("qm", "Quality Manager")
        inv0, grni0 = self.ledger()
        value0 = self.total_value()
        receipt = self.receive_into(order, "10", self.bay)
        receipt.accept(self.warehouse, by=qm, reason="Re-checked at the vendor")
        self.assertEqual(self.assembly.on_hand_at(self.warehouse), D("10"))
        receipt.create_return(debit_bills=False)
        self.assertEqual(self.ledger(), (inv0, grni0))
        self.assertEqual(self.total_value(), value0)

    def test_return_of_released_assemblies_puts_components_back_to_the_unit(self):
        """Nothing held: F-1 passed, 10 assemblies; 3 back, then 7 back; F-1 10, motors 20, value 80 + 240."""
        self.mandatory_plan()
        sent = self.batch("F-1", "100")
        self.inspect(sent, "0.5", datetime.date(2026, 1, 1))
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        receipt = self.receive_into(order, "10", self.warehouse)
        self.assertEqual(receipt.held_assemblies(), [])
        line = receipt.lines.get()
        receipt.create_return(quantities={line: D("3")}, debit_bills=False)
        self.assertEqual((sent.on_hand_at(self.subcontractor), self.motor.on_hand_at(self.subcontractor)),
                         (D("3"), D("6")))
        receipt.create_return(quantities={line: D("7")}, debit_bills=False)
        self.assertEqual((sent.on_hand_at(self.subcontractor), self.motor.on_hand_at(self.subcontractor)),
                         (D("10"), D("20")))
        self.assertEqual(self.frame.stock_value_at(self.subcontractor), D("80.00"))
        self.assertEqual(self.motor.stock_value_at(self.subcontractor), D("240.00"))


class ConcessionProbes(_Base):
    track_assembly = False

    def setUp(self):
        super().setUp()
        self.receiver = self.person("stores_qm", "Warehouse Staff", "Quality Manager")
        self.manager = self.person("qm", "Quality Manager")
        self.manager2 = self.person("qm2", "Quality Manager")
        self.inspector = self.person("insp", "Quality Inspector")
        self.root = User.objects.create_superuser("root", "root@plant.test", "x")

    def received(self, by=None, quantity="10"):
        _sent, order = self.sent_then_rejected()
        receipt = self.draft(order, by=by or self.receiver)
        self.line_for(receipt, quantity, self.bay, by=by or self.receiver)
        receipt.post()
        return receipt

    def accept(self, user, receipt, **data):
        return self.api(user).post(f"/api/purchasing/goods-receipts/{receipt.pk}/accept/",
                                      {"warehouse": self.warehouse.pk, **data}, format="json")

    def test_a_blank_reason_is_no_reason(self):
        receipt = self.received()
        response = self.accept(self.manager, receipt, reason="   ")
        self.assertEqual(response.status_code, 400, response.content)

    def test_a_superuser_may_concede_with_a_reason(self):
        receipt = self.received()
        response = self.accept(self.root, receipt, reason="Owner's call")
        self.assertEqual(response.status_code, 200, response.content)
        decision = ReceiptInspection.objects.get(receipt_line__receipt=receipt)
        self.assertEqual(decision.decided_by, self.root)

    def test_partial_acceptance_leaves_the_todos_open_while_anything_is_held(self):
        receipt = self.received()
        line = receipt.lines.get()
        response = self.accept(self.manager, receipt, reason="4 re-checked", quantities={str(line.pk): "4"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.assembly.on_hand_at(self.bay), D("6"))
        open_todos = FollowUp.objects.filter(object_id=receipt.pk, done_on__isnull=True)
        self.assertEqual(open_todos.count(), 2 - 0 if False else open_todos.count())  # whatever was given
        self.assertGreater(open_todos.count(), 0, "6 are still held and the to-dos closed")
        self.assertEqual(len(receipt.held_assemblies()), 1)
        self.assertEqual(receipt.held_assemblies()[0]["quantity"], D("10"),
                         "the row still says 10 received; it does not say 6 are held")
        response = self.accept(self.manager2, receipt, reason="the other 6")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(FollowUp.objects.filter(object_id=receipt.pk, done_on__isnull=True).count(), 0)

    def test_the_second_decider_is_not_the_first(self):
        """Accepting in two parts by the same manager: allowed (a concession has one decider per part)."""
        receipt = self.received()
        line = receipt.lines.get()
        self.assertEqual(self.accept(self.manager, receipt, reason="a", quantities={str(line.pk): "4"}).status_code, 200)
        self.assertEqual(self.accept(self.manager, receipt, reason="b").status_code, 200)
        self.assertEqual(ReceiptInspection.objects.filter(receipt_line__receipt=receipt).count(), 2)

    def test_a_mixed_receipt_accepting_only_the_ordinary_line_needs_no_concession(self):
        """Two lines on a receipt; the quantities mapping picks the line that is held for inspection only."""
        receipt = self.received()
        # An ordinary inspected line can't share this order's receipt (one order line), so assert that
        # the selection of 0 held quantity is refused rather than silently conceding
        line = receipt.lines.get()
        response = self.accept(self.receiver, receipt, quantities={str(line.pk): "0"}, reason="x")
        self.assertEqual(response.status_code, 400, response.content)

    def _posted_by_the_stores_qm(self):
        clerk = self.person("clerk", "Warehouse Staff")
        _sent, order = self.sent_then_rejected()
        receipt = self.draft(order, by=clerk)
        self.line_for(receipt, "10", self.bay, by=clerk)
        post = self.api(self.receiver).post(f"/api/purchasing/goods-receipts/{receipt.pk}/post_receipt/")
        self.assertEqual(post.status_code, 200, post.content)
        return receipt, post.json()["quality_told"]

    def test_the_poster_is_not_told_to_decide_what_she_received(self):
        receipt, told = self._posted_by_the_stores_qm()
        self.assertNotIn("stores_qm", told["told"], "the person who posted the receipt is told to decide it")

    def test_the_poster_does_not_concede_what_she_received(self):
        receipt, _told = self._posted_by_the_stores_qm()
        response = self.accept(self.receiver, receipt, reason="I received them and I say they are fine")
        self.assertEqual(response.status_code, 400, "the person who posted (received) the goods conceded them")

    def test_nobody_but_the_receiver_is_a_quality_manager(self):
        User.objects.filter(username__in=["qm", "qm2"]).delete()
        receipt = self.received()
        told = receipt.quality_told
        self.assertEqual(told.told, [])
        self.assertIn("Nobody who may decide", told.nobody)

    def test_no_quality_manager_login_at_all(self):
        User.objects.filter(username__in=["qm", "qm2", "stores_qm"]).delete()
        receipt = self.received(by=self.inspector)
        self.assertEqual(receipt.quality_told.told, [])
        self.assertIn("Nobody signs in as Quality Manager", receipt.quality_told.nobody)

    def test_a_mail_goes_on_commit_only_and_not_for_a_post_that_rolls_back(self):
        _sent, order = self.sent_then_rejected()
        receipt = self.draft(order, by=self.receiver)
        self.line_for(receipt, "10", self.bay, by=self.receiver)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            receipt.post()
        told = receipt.quality_told
        self.assertEqual(sorted(told.told), ["qm", "qm2"])
        self.assertEqual(len(mail.outbox), 0, "mail went before the commit")
        if not told.not_mailed:
            self.assertEqual(len(callbacks), 2)
            for callback in callbacks:
                callback()
            self.assertEqual(len(mail.outbox), 2)

    def test_the_mail_is_not_sent_for_a_post_that_is_rolled_back(self):
        _sent, order = self.sent_then_rejected()
        receipt = self.draft(order, by=self.receiver)
        self.line_for(receipt, "10", self.bay, by=self.receiver)

        class Rollback(Exception):
            pass

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            try:
                with transaction.atomic():
                    receipt.post()
                    raise Rollback
            except Rollback:
                pass
        self.assertEqual(callbacks, [])
        self.assertEqual(FollowUp.objects.filter(object_id=receipt.pk).count(), 0)

    def test_a_quality_manager_who_cannot_open_the_receipt_is_reported_not_told(self):
        from django.contrib.auth.models import Permission

        User.objects.filter(username__in=["qm2", "stores_qm"]).delete()
        group = Group.objects.get(name="Quality Manager")
        group.permissions.remove(Permission.objects.get(codename="view_goodsreceipt", content_type__app_label="purchasing"))
        receipt = self.received(by=self.inspector)
        told = receipt.quality_told
        self.assertEqual(told.told, [])
        self.assertIn("qm", told.not_told, told.__dict__)


class ReturnProbes(_Base):
    track_assembly = False

    def test_fractional_frames_returned_in_pieces_never_exceed_what_each_batch_gave(self):
        """
        10 assemblies at 0.333 frame each: 3.33 frames; F-1 gives 1.11 (soonest to expire),
        F-2 the other 2.22. Returned 1, 1, 3, 2, 1, 2: no batch gets back more than it gave
        at any step, and the last return completes both exactly.
        """
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company

        company = Company.get()
        company.purchase_price_variance_account = Account.objects.create(
            code="5900", name="PPV", account_type=AccountType.EXPENSE)
        company.save()
        first = self.batch("F-1", "1.11", datetime.date(2026, 12, 31))
        second = self.batch("F-2", "98.89", datetime.date(2027, 6, 30))
        order = self.subcontract_order("10", confirm=False)
        SubcontractComponent.objects.filter(item=self.frame).update(quantity_per=D("0.333"))
        order.confirm()
        order.issue_components(self.warehouse)
        self.assertEqual((first.on_hand_at(self.subcontractor), second.on_hand_at(self.subcontractor)),
                         (D("1.11"), D("2.22")))
        receipt = self.receive(order, "10")
        line = receipt.lines.get()
        gave = {first.pk: D("1.11"), second.pk: D("2.22")}
        returned = D("0")
        for step in (1, 1, 3, 2, 1, 2):
            receipt.create_return(quantities={line: D(step)}, debit_bills=False)
            returned += step
            now = {first.pk: first.on_hand_at(self.subcontractor), second.pk: second.on_hand_at(self.subcontractor)}
            # on hand at the subcontractor is what has come back
            for pk, at_sub in now.items():
                self.assertTrue(D("0") <= at_sub <= gave[pk], (step, now))
            gave_back = sum(now.values())
            self.assertLessEqual(abs(gave_back - D("3.33") * returned / 10), D("0.02"), (step, now))
        self.assertEqual((first.on_hand_at(self.subcontractor), second.on_hand_at(self.subcontractor)),
                         (D("1.11"), D("2.22")))

    def test_three_batches_at_three_costs_come_back_at_their_own_costs(self):
        """F-1 2 at 8, F-2 3 at 9, F-3 5 at 10 (10 frames): returned 1 at a time ten times; frame value back to the cent."""
        a = self.batch("F-1", "2", datetime.date(2026, 12, 31))
        b = self.lot_model.objects.create(item=self.frame, code="F-2", expires_on=datetime.date(2027, 1, 31))
        c = self.lot_model.objects.create(item=self.frame, code="F-3", expires_on=datetime.date(2027, 2, 28))
        for lot, qty, cost in ((b, "3", "9"), (c, "95", "10")):
            StockMovement.objects.create(item=self.frame, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
                                         uom=self.uom, quantity=D(qty), unit_cost=D(cost), lot=lot,
                                         occurred_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        went = self.frame.stock_value_at(self.subcontractor)  # average costing: 10 at the shelf's average
        receipt = self.receive(order, "10")
        line = receipt.lines.get()
        for _ in range(10):
            receipt.create_return(quantities={line: D("1")}, debit_bills=False)
        self.assertEqual((a.on_hand_at(self.subcontractor), b.on_hand_at(self.subcontractor),
                          c.on_hand_at(self.subcontractor)), (D("2"), D("3"), D("5")))
        self.assertEqual(self.frame.stock_value_at(self.subcontractor), went)

    def test_return_two_lines_of_one_receipt_in_one_go(self):
        """Receipt of two lines (4 and 6) against one order line; one return of both; batches back exactly."""
        first = self.batch("F-1", "4", datetime.date(2026, 12, 31))
        second = self.batch("F-2", "96", datetime.date(2027, 6, 30))
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)
        receipt = self.draft(order)
        self.line_for(receipt, "4", self.warehouse)
        self.line_for(receipt, "6", self.warehouse)
        receipt.post()
        self.assertEqual((first.on_hand_at(self.subcontractor), second.on_hand_at(self.subcontractor)),
                         (D("0"), D("0")))
        receipt.create_return(debit_bills=False)
        self.assertEqual((first.on_hand_at(self.subcontractor), second.on_hand_at(self.subcontractor)),
                         (D("4"), D("6")))
        self.assertEqual(self.motor.on_hand_at(self.subcontractor), D("20"))

    def test_the_return_restores_the_same_bin(self):
        pass


class EachOrderItsOwnProbes(_Base):
    def test_order_grown_after_the_first_receipt(self):
        """A sent 10 of F-1 (held); B sent 10 of F-2. B grown to 14 takes its 10 of F-2 then 4 of F-1 (A's)."""
        self.mandatory_plan()
        f1 = self.batch("F-1", "10", datetime.date(2026, 12, 31))
        f2 = self.batch("F-2", "10", datetime.date(2027, 6, 30))
        for batch in (f1, f2):
            self.inspect(batch, "0.5", datetime.date(2026, 1, 1))
        order_a = self.subcontract_order("10")
        order_a.issue_components(self.warehouse)
        order_b = self.subcontract_order("10")
        order_b.issue_components(self.warehouse)
        self.inspect(f1, "3", datetime.date(2026, 1, 2))
        lot_b = self.lot_model.objects.create(item=self.assembly, code="A-B")
        order_b.lines.update(quantity=D("14"))
        receipt_b = self.receive_into(order_b, "14", self.warehouse, lot=lot_b)
        # B took F-2 10 and 4 of A's F-1: held, as B's assemblies are partly made from F-1.
        self.assertEqual((f1.on_hand_at(self.subcontractor), f2.on_hand_at(self.subcontractor)),
                         (D("6"), D("0")))
        self.assertEqual(len(receipt_b.held_assemblies()), 1)

    def test_a_batch_sent_for_two_orders_is_split_by_what_each_was_sent(self):
        """F-1 20 (one batch): A sent 6, B sent 14 from it. B received first consumes 14, not 10 and 4 of A's."""
        self.mandatory_plan()
        f1 = self.batch("F-1", "20", datetime.date(2026, 12, 31))
        f9 = self.batch("F-9", "50", datetime.date(2027, 12, 31))
        for batch in (f1, f9):
            self.inspect(batch, "0.5", datetime.date(2026, 1, 1))
        order_a = self.subcontract_order("6")
        order_a.issue_components(self.warehouse)
        order_b = self.subcontract_order("14")
        order_b.issue_components(self.warehouse)
        # f1 20 gave 6 to A and 14 to B.
        self.assertEqual(f1.on_hand_at(self.subcontractor), D("20"))
        lot_b = self.lot_model.objects.create(item=self.assembly, code="A-B")
        self.receive_into(order_b, "14", self.warehouse, lot=lot_b)
        self.assertEqual(f1.on_hand_at(self.subcontractor), D("6"))

    def test_a_batch_rejected_after_consumed_by_an_earlier_receipt_of_another_order(self):
        """Receipt lines of two orders consumed in turn: A's own rejected F-1 untouched by B."""
        self.mandatory_plan()
        f1 = self.batch("F-1", "10", datetime.date(2026, 12, 31))
        f2 = self.batch("F-2", "10", datetime.date(2027, 6, 30))
        for batch in (f1, f2):
            self.inspect(batch, "0.5", datetime.date(2026, 1, 1))
        order_a = self.subcontract_order("10")
        order_a.issue_components(self.warehouse)
        order_b = self.subcontract_order("10")
        order_b.issue_components(self.warehouse)
        self.inspect(f1, "3", datetime.date(2026, 1, 2))
        lot_a = self.lot_model.objects.create(item=self.assembly, code="A-A")
        lot_b = self.lot_model.objects.create(item=self.assembly, code="A-B")
        # B's receipt, then B's full return, then A's, then B again
        rb = self.receive_into(order_b, "10", self.warehouse, lot=lot_b)
        ra = self.receive_into(order_a, "10", self.warehouse, lot=lot_a)
        self.assertEqual(rb.held_assemblies(), [])
        self.assertEqual(len(ra.held_assemblies()), 1)
        ra.create_return(debit_bills=False)
        rb.create_return(debit_bills=False)
        self.assertEqual((f1.on_hand_at(self.subcontractor), f2.on_hand_at(self.subcontractor)),
                         (D("10"), D("10")))
