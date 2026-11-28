"""
O458, shared rule B on purchasing's documents known by their status: a line is changed, added or removed only while
both the document it leaves and the one it joins take changes, and both are held first. Each guarded its own way
and asked only the document in hand, so a line moved off a confirmed agreement, an approved requisition or a request
out with vendors onto a draft left the first short of what it had committed to; and a cancelled order took lines.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import (
    BlanketOrder,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    RequestForQuotation,
)
from .tests_blanket import BlanketTestCase
from .tests_lifecycle import PurchasingLifecycleTestCase
from .tests_requisitions import RequisitionTestCase
from .tests_rfq import RfqTestCase


class AgreementLineLeavesNoConfirmedAgreementTests(BlanketTestCase):
    def test_a_line_is_not_moved_off_a_confirmed_agreement_onto_a_draft(self):
        confirmed = self.agreement()
        draft = BlanketOrder.objects.create(vendor=self.vendor, start_date=datetime.date(2026, 1, 1),
                                            end_date=datetime.date(2026, 12, 31), currency=self.usd)
        self.line.blanket = draft
        with self.assertRaisesMessage(ValidationError, f"{confirmed} is confirmed"):
            self.line.save()
        self.assertEqual(confirmed.lines.count(), 1)


class RequisitionLineLeavesNoApprovedRequisitionTests(RequisitionTestCase):
    def test_a_line_is_not_moved_off_an_approved_requisition_onto_a_draft(self):
        approved = self.requisition()
        approved.approve(by=self.manager)
        draft = PurchaseRequisition.objects.create(requested_by=self.employee, request_date=datetime.date(2026, 1, 1),
                                                   needed_by=datetime.date(2026, 2, 1))
        self.line.requisition = draft
        with self.assertRaisesMessage(ValidationError, f"{approved} is approved"):
            self.line.save()
        self.assertEqual(approved.lines.count(), 1)


class RequestLinesLeaveNoRequestOutWithVendorsTests(RfqTestCase):
    def draft(self):
        return RequestForQuotation.objects.create(issue_date=datetime.date(2026, 1, 1),
                                                  response_due=datetime.date(2026, 1, 15), currency=self.usd)

    def test_a_line_is_not_moved_off_a_request_sent_onto_a_draft(self):
        sent = self.rfq()
        self.line.rfq = self.draft()
        with self.assertRaisesMessage(ValidationError, f"{sent} is sent"):
            self.line.save()
        self.assertEqual(sent.lines.count(), 1)

    def test_a_vendor_asked_is_not_moved_off_a_request_sent_onto_a_draft(self):
        sent = self.rfq()
        invitation = self.invitations[0]
        invitation.rfq = self.draft()
        with self.assertRaisesMessage(ValidationError, f"{sent} is sent"):
            invitation.save()
        self.assertEqual(sent.invited.count(), 3)

    def test_a_vendor_asked_still_declines_while_the_request_is_out(self):
        self.rfq()
        self.invitations[0].decline("No capacity")
        self.assertTrue(self.invitations[0].declined)


class CancelledOrderTakesNoLineTests(PurchasingLifecycleTestCase):
    def cancelled(self):
        order = self.make_order(confirm=False)
        order.cancel()
        return PurchaseOrder.objects.get(pk=order.pk)

    def test_a_cancelled_order_takes_no_new_line(self):
        order = self.cancelled()
        with self.assertRaisesMessage(ValidationError, f"{order} is cancelled"):
            PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
                                             unit_price=Decimal("5"))

    def test_a_cancelled_orders_line_is_neither_deleted_nor_moved_onto_a_draft(self):
        order = self.cancelled()
        line = order.lines.get()
        with self.assertRaisesMessage(ValidationError, f"{order} is cancelled"):
            line.delete()
        line.order = self.make_order(confirm=False)
        with self.assertRaisesMessage(ValidationError, f"{order} is cancelled"):
            line.save()
        self.assertEqual(order.lines.count(), 1)
