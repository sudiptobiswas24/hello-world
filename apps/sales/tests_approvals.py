"""
Discount and margin approval.

RBAC answers "who may confirm an order". It says nothing about how much
they may give away while doing it: a rep with permission to confirm can
discount 90% and sell below cost, and no role check anywhere notices.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import (
    ApprovalPolicy,
    ApprovalStatus,
    CustomerProfile,
    OrderStatus,
    SalesOrderLine,
)
from .tests_base import SalesTestCase


class ApprovalTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.approver = get_user_model().objects.create_user(
            username="controller", password="x"
        )

    def draft(self, quantity="10", price="100", discount="0"):
        from .models import SalesOrder

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
            unit_price=Decimal(price), discount_percent=Decimal(discount),
            revenue_account=self.revenue,
        )
        return order


class NoPolicyTests(ApprovalTestCase):
    def test_nothing_is_enforced_until_a_policy_exists(self):
        """Switching this on must not retroactively block every order."""
        order = self.draft(discount="90")
        self.assertEqual(order.approval_reasons(), [])
        self.assertEqual(order.approval_status(), ApprovalStatus.NOT_REQUIRED)
        order.confirm()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)

    def test_a_blank_threshold_is_not_a_zero_threshold(self):
        ApprovalPolicy.objects.create(code="P", name="Policy")
        order = self.draft(discount="90")
        self.assertEqual(order.approval_reasons(), [])


class DiscountLimitTests(ApprovalTestCase):
    def setUp(self):
        super().setUp()
        ApprovalPolicy.objects.create(
            code="STD", name="Standard", max_discount_percent=Decimal("15")
        )

    def test_a_discount_within_the_limit_passes(self):
        order = self.draft(discount="10")
        order.confirm()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)

    def test_a_discount_above_the_limit_blocks_confirmation(self):
        order = self.draft(discount="40")
        self.assertEqual(order.approval_status(), ApprovalStatus.PENDING)
        with self.assertRaisesMessage(ValidationError, "needs approval"):
            order.confirm()

    def test_the_reason_names_the_line_and_the_number(self):
        order = self.draft(discount="40")
        reason = order.approval_reasons()[0]
        self.assertIn("Widget", reason)
        self.assertIn("40", reason)
        self.assertIn("15", reason)

    def test_approving_lets_it_through_and_records_who(self):
        order = self.draft(discount="40")
        order.approve(by=self.approver, note="Volume deal")

        order.confirm()

        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        self.assertEqual(order.approved_by, self.approver)
        self.assertEqual(order.approval_note, "Volume deal")
        self.assertEqual(order.approval_status(), ApprovalStatus.APPROVED)

    def test_the_note_defaults_to_what_was_being_approved(self):
        order = self.draft(discount="40")
        order.approve(by=self.approver)
        self.assertIn("discounted 40", order.approval_note)

    def test_approving_a_compliant_order_is_refused(self):
        """Approving everything by reflex would make the record meaningless."""
        order = self.draft(discount="5")
        with self.assertRaisesMessage(ValidationError, "breaches no policy"):
            order.approve(by=self.approver)

    def test_it_cannot_be_approved_twice(self):
        order = self.draft(discount="40")
        order.approve(by=self.approver)
        with self.assertRaisesMessage(ValidationError, "already been approved"):
            order.approve(by=self.approver)

    def test_a_cancelled_order_cannot_be_approved(self):
        order = self.draft(discount="40")
        order.cancel()
        with self.assertRaisesMessage(ValidationError, "cancelled order cannot be approved"):
            order.approve(by=self.approver)


class ApprovalStalenessTests(ApprovalTestCase):
    """An approval covers the order someone actually looked at."""

    def setUp(self):
        super().setUp()
        ApprovalPolicy.objects.create(
            code="STD", name="Standard", max_discount_percent=Decimal("15")
        )

    def test_re_pricing_after_approval_drops_it(self):
        order = self.draft(discount="20")
        order.approve(by=self.approver)

        line = order.lines.get()
        line.discount_percent = Decimal("60")
        line.save()

        order.refresh_from_db()
        self.assertIsNone(order.approved_at)
        self.assertEqual(order.approval_status(), ApprovalStatus.PENDING)
        with self.assertRaisesMessage(ValidationError, "needs approval"):
            order.confirm()

    def test_changing_the_quantity_drops_it_too(self):
        order = self.draft(discount="20")
        order.approve(by=self.approver)

        line = order.lines.get()
        line.quantity = Decimal("100")
        line.save()

        order.refresh_from_db()
        self.assertIsNone(order.approved_at)

    def test_an_untouched_order_keeps_its_approval(self):
        order = self.draft(discount="20")
        order.approve(by=self.approver)

        line = order.lines.get()
        line.description = "Widget, blue"
        line.save()

        order.refresh_from_db()
        self.assertIsNotNone(order.approved_at)


class MarginFloorTests(ApprovalTestCase):
    """The check a percentage limit misses: a small discount on a
    thin-margin item is still a sale at a loss."""

    def setUp(self):
        super().setUp()
        ApprovalPolicy.objects.create(
            code="STD", name="Standard", max_discount_percent=Decimal("50"),
            min_margin_percent=Decimal("20"),
        )

    def test_a_healthy_margin_passes(self):
        order = self.draft("10", "100")  # cost 4, price 100
        self.assertEqual(order.margin_percent(), Decimal("96.00"))
        order.confirm()

    def test_selling_below_the_floor_needs_approval(self):
        order = self.draft("10", "4.50")  # cost 4, margin ~11%
        self.assertEqual(order.approval_status(), ApprovalStatus.PENDING)
        self.assertIn("Gross margin", order.approval_reasons()[0])

    def test_a_small_discount_can_still_trip_it(self):
        """Within the 50% discount limit, and still under water."""
        order = self.draft("10", "5", discount="10")
        self.assertEqual(order.approval_reasons(), [
            reason for reason in order.approval_reasons() if "margin" in reason
        ])

    def test_uncosted_lines_are_left_out_of_both_sides(self):
        """A pure-service order should read as neither 100% nor 0% margin."""
        from apps.inventory.models import Item

        service = Item.objects.create(
            sku="SVC-1", name="Consulting", uom=self.uom, track_inventory=False
        )
        from .models import SalesOrder

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        SalesOrderLine.objects.create(
            order=order, item=service, uom=self.uom, quantity=Decimal("1"),
            unit_price=Decimal("500"), revenue_account=self.revenue,
        )
        self.assertIsNone(order.margin_percent())
        self.assertEqual(order.approval_reasons(), [])


class OrderValueTests(ApprovalTestCase):
    def test_a_large_order_needs_approval_whatever_the_margin(self):
        ApprovalPolicy.objects.create(
            code="STD", name="Standard", max_order_value=Decimal("5000")
        )
        order = self.draft("100", "100")
        self.assertIn("above the 5000", order.approval_reasons()[0])


class CreditLimitAsApprovalTests(ApprovalTestCase):
    def test_over_the_limit_is_now_an_approval_reason(self):
        CustomerProfile.objects.create(party=self.customer, credit_limit=Decimal("500"))
        order = self.draft("10", "100")

        self.assertEqual(order.approval_status(), ApprovalStatus.PENDING)
        self.assertIn("credit limit", order.approval_reasons()[0])

        order.approve(by=self.approver)
        order.confirm()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)

    def test_every_breach_is_listed_at_once(self):
        """An approver needs to see all of it, not discover the next one
        each time the last is fixed."""
        CustomerProfile.objects.create(party=self.customer, credit_limit=Decimal("500"))
        ApprovalPolicy.objects.create(
            code="STD", name="Standard", max_discount_percent=Decimal("15"),
            max_order_value=Decimal("100"),
        )
        order = self.draft("10", "100", discount="40")

        self.assertEqual(len(order.approval_reasons()), 3)


class QuotationApprovalTests(ApprovalTestCase):
    def quote(self, discount="40"):
        from .models import Quotation, QuotationLine

        quotation = Quotation.objects.create(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
            valid_until=datetime.date(2027, 4, 1), currency=self.usd,
        )
        QuotationLine.objects.create(
            quotation=quotation, item=self.item, uom=self.uom, quantity=Decimal("10"),
            unit_price=Decimal("100"), discount_percent=Decimal(discount),
            revenue_account=self.revenue,
        )
        return quotation

    def setUp(self):
        super().setUp()
        ApprovalPolicy.objects.create(
            code="STD", name="Standard", max_discount_percent=Decimal("15")
        )

    def test_accepting_an_over_discounted_quote_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "needs approval"):
            self.quote().accept()

    def test_it_can_be_accepted_and_approved_in_one_step(self):
        order = self.quote().accept(approve_as=self.approver)

        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        self.assertEqual(order.approved_by, self.approver)
        self.assertIn("quotation acceptance", order.approval_note)

    def test_a_compliant_quote_needs_no_approver(self):
        order = self.quote(discount="5").accept()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        self.assertIsNone(order.approved_at)
