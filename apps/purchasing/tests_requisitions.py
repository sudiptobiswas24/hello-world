"""
Purchase requisitions.

A control gap, not a convenience one. Approval on the purchase order
asks "may we commit this money" at the moment the buyer is already
negotiating. The question that needs answering first is "does the
company want this at all", and it needs answering by whoever owns the
budget, not by whoever found the supplier.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .models import (
    OrderStatus,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    RequisitionStatus,
    VendorPrice,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class RequisitionTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.employee = Party.objects.create(code="E-1", name="Dana")
        PartyRoleAssignment.objects.create(party=self.employee, role=PartyRole.EMPLOYEE)
        self.manager = get_user_model().objects.create_user(username="budget", password="x")

    def requisition(self, quantity="10", price="5", submit=True):
        requisition = PurchaseRequisition.objects.create(
            requested_by=self.employee, request_date=datetime.date(2026, 1, 1),
            needed_by=datetime.date(2026, 2, 1),
        )
        self.line = PurchaseRequisitionLine.objects.create(
            requisition=requisition, item=self.item, uom=self.uom,
            quantity=Decimal(quantity),
            estimated_price=Decimal(price) if price is not None else None,
        )
        if submit:
            requisition.submit()
        return requisition


class RequisitionLifecycleTests(RequisitionTestCase):
    def test_submitting_numbers_it(self):
        requisition = self.requisition()
        self.assertEqual(requisition.number, "REQ-2026-00001")
        self.assertEqual(requisition.status, RequisitionStatus.SUBMITTED)

    def test_a_draft_has_no_number(self):
        self.assertEqual(self.requisition(submit=False).number, "")

    def test_an_empty_requisition_cannot_be_submitted(self):
        requisition = PurchaseRequisition.objects.create(
            requested_by=self.employee, request_date=datetime.date(2026, 1, 1)
        )
        with self.assertRaisesMessage(ValidationError, "no lines"):
            requisition.submit()

    def test_the_requester_must_be_an_employee(self):
        outsider = Party.objects.create(code="X-1", name="Someone")
        with self.assertRaisesMessage(ValidationError, "does not have the Employee role"):
            PurchaseRequisition(
                requested_by=outsider, request_date=datetime.date(2026, 1, 1)
            ).clean()

    def test_approving_records_who_and_when(self):
        requisition = self.requisition()
        requisition.approve(by=self.manager, note="Budgeted")

        self.assertEqual(requisition.status, RequisitionStatus.APPROVED)
        self.assertEqual(requisition.decided_by, self.manager)
        self.assertEqual(requisition.decision_note, "Budgeted")
        self.assertIsNotNone(requisition.decided_at)

    def test_rejecting_needs_a_reason(self):
        """A requisition that comes back with no explanation gets
        resubmitted unchanged, wasting everyone's time twice."""
        requisition = self.requisition()
        with self.assertRaisesMessage(ValidationError, "Say why"):
            requisition.reject(by=self.manager)

        requisition.reject(by=self.manager, note="Use existing stock")
        self.assertEqual(requisition.status, RequisitionStatus.REJECTED)

    def test_only_a_submitted_requisition_can_be_decided(self):
        requisition = self.requisition(submit=False)
        with self.assertRaisesMessage(ValidationError, "Only a submitted requisition"):
            requisition.approve(by=self.manager)

    def test_it_cannot_be_submitted_twice(self):
        requisition = self.requisition()
        with self.assertRaisesMessage(ValidationError, "already submitted"):
            requisition.submit()

    def test_the_estimated_total_adds_up(self):
        self.assertEqual(self.requisition("10", "5").estimated_total(), Decimal("50"))

    def test_an_estimate_can_come_from_an_agreed_price(self):
        VendorPrice.objects.create(
            vendor=self.vendor, item=self.item, currency=self.usd, unit_price=Decimal("4")
        )
        requisition = self.requisition("10", price=None, submit=False)
        self.line.suggested_vendor = self.vendor
        self.line.save()

        self.assertEqual(requisition.estimated_total(), Decimal("40"))

    def test_cancelling(self):
        requisition = self.requisition()
        requisition.cancel()
        self.assertEqual(requisition.status, RequisitionStatus.CANCELLED)


class RequisitionToOrderTests(RequisitionTestCase):
    def approved(self, quantity="10", price="5"):
        requisition = self.requisition(quantity, price)
        requisition.approve(by=self.manager)
        return requisition

    def test_an_approved_requisition_becomes_an_order(self):
        requisition = self.approved()

        order = requisition.create_order(self.vendor, order_date=datetime.date(2026, 1, 10))

        self.assertEqual(order.vendor, self.vendor)
        self.assertEqual(order.lines.get().quantity, Decimal("10"))
        self.assertEqual(order.reference, requisition.number)

    def test_the_order_price_comes_from_the_agreement_not_the_estimate(self):
        """The requester guessed; the vendor agreed. The agreement wins."""
        VendorPrice.objects.create(
            vendor=self.vendor, item=self.item, currency=self.usd, unit_price=Decimal("4")
        )
        requisition = self.approved("10", "5")

        order = requisition.create_order(self.vendor, order_date=datetime.date(2026, 1, 10))

        self.assertEqual(order.lines.get().unit_price, Decimal("4"))

    def test_the_estimate_is_used_when_nothing_is_agreed(self):
        requisition = self.approved("10", "5")
        order = requisition.create_order(self.vendor, order_date=datetime.date(2026, 1, 10))
        self.assertEqual(order.lines.get().unit_price, Decimal("5"))

    def test_an_unapproved_requisition_cannot_be_ordered(self):
        requisition = self.requisition()
        with self.assertRaisesMessage(ValidationError, "Only an approved requisition"):
            requisition.create_order(self.vendor)

    def test_ordering_everything_closes_the_requisition(self):
        requisition = self.approved()
        requisition.create_order(self.vendor, order_date=datetime.date(2026, 1, 10))
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, RequisitionStatus.ORDERED)

    def test_it_can_be_split_across_vendors(self):
        """Which is exactly why the vendor is not chosen when the request
        is made."""
        other = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)

        requisition = self.approved("10", "5")
        second_line = PurchaseRequisitionLine.objects.create(
            requisition=requisition, item=self.item, uom=self.uom,
            quantity=Decimal("4"), estimated_price=Decimal("5"),
        )

        first = requisition.create_order(self.vendor, lines=[self.line],
                                         order_date=datetime.date(2026, 1, 10))
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, RequisitionStatus.APPROVED)

        second = requisition.create_order(other, lines=[second_line],
                                          order_date=datetime.date(2026, 1, 10))
        requisition.refresh_from_db()

        self.assertEqual(first.lines.get().quantity, Decimal("10"))
        self.assertEqual(second.lines.get().quantity, Decimal("4"))
        self.assertEqual(requisition.status, RequisitionStatus.ORDERED)

    def test_the_same_line_is_not_ordered_twice(self):
        requisition = self.approved()
        requisition.create_order(self.vendor, order_date=datetime.date(2026, 1, 10))

        with self.assertRaisesMessage(ValidationError, "already been ordered"):
            requisition.create_order(self.vendor, order_date=datetime.date(2026, 1, 11))

    def test_a_cancelled_order_releases_the_request_again(self):
        requisition = self.approved()
        order = requisition.create_order(self.vendor, order_date=datetime.date(2026, 1, 10))

        order.cancel()

        self.assertEqual(self.line.quantity_ordered(), Decimal("0"))
        requisition.status = RequisitionStatus.APPROVED
        requisition.save(update_fields=["status"])
        requisition.create_order(self.vendor, order_date=datetime.date(2026, 1, 11))

    def test_an_ordered_requisition_cannot_be_cancelled(self):
        requisition = self.approved()
        requisition.create_order(self.vendor, order_date=datetime.date(2026, 1, 10))
        requisition.refresh_from_db()

        with self.assertRaisesMessage(ValidationError, "Cancel the purchase order instead"):
            requisition.cancel()

    def test_the_order_traces_back_to_the_request(self):
        requisition = self.approved()
        order = requisition.create_order(self.vendor, order_date=datetime.date(2026, 1, 10))
        self.assertEqual(order.lines.get().requisition_line, self.line)

    def test_a_non_vendor_cannot_be_ordered_from(self):
        requisition = self.approved()
        with self.assertRaisesMessage(ValidationError, "does not have the Vendor role"):
            requisition.create_order(self.employee)


class SuggestedVendorTests(RequisitionTestCase):
    def test_it_suggests_from_the_agreed_prices(self):
        VendorPrice.objects.create(
            vendor=self.vendor, item=self.item, currency=self.usd,
            unit_price=Decimal("4"), is_preferred=True,
        )
        requisition = self.requisition()
        self.assertEqual(requisition.suggested_vendors()[self.line], self.vendor)

    def test_the_requester_s_own_suggestion_wins(self):
        other = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        VendorPrice.objects.create(
            vendor=self.vendor, item=self.item, currency=self.usd,
            unit_price=Decimal("4"), is_preferred=True,
        )
        requisition = self.requisition(submit=False)
        self.line.suggested_vendor = other
        self.line.save()

        self.assertEqual(requisition.suggested_vendors()[self.line], other)
