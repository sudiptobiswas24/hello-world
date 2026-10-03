"""
Agreed vendor prices, and spend approval.

Purchase lines used to demand a hand-typed price on the reasoning that
"the price is whatever the vendor quoted". True of a one-off, wrong of
everything else — and it left the price leg of the three-way match with
nothing to check against but what someone typed on the order.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.core.approvals import ApprovalStatus
from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .models import (
    OrderStatus,
    PurchaseApprovalPolicy,
    PurchaseOrder,
    PurchaseOrderLine,
    VendorPrice,
)
from .pricing import preferred_vendor, resolve_lead_time, resolve_purchase_price
from .tests_lifecycle import PurchasingLifecycleTestCase


class VendorPriceTestCase(PurchasingLifecycleTestCase):
    def agree(self, price, min_quantity="0", **kwargs):
        return VendorPrice.objects.create(
            vendor=kwargs.pop("vendor", self.vendor), item=kwargs.pop("item", self.item),
            currency=self.usd, unit_price=Decimal(price),
            min_quantity=Decimal(min_quantity), **kwargs
        )

    def draft_line(self, quantity="10", price=None):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        return PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity),
            unit_price=Decimal(price) if price is not None else None,
        )


class PriceResolutionTests(VendorPriceTestCase):
    def test_a_line_takes_the_agreed_price(self):
        self.agree("4.50")
        self.assertEqual(self.draft_line("10").unit_price, Decimal("4.50"))

    def test_an_explicit_price_still_wins(self):
        self.agree("4.50")
        self.assertEqual(self.draft_line("10", price="6").unit_price, Decimal("6"))

    def test_a_quantity_break_applies_when_reached(self):
        """A buyer ordering 1,000 should get the 1,000 price without
        having to remember it exists."""
        self.agree("5.00")
        self.agree("4.00", min_quantity="100")

        self.assertEqual(self.draft_line("10").unit_price, Decimal("5.00"))
        self.assertEqual(self.draft_line("150").unit_price, Decimal("4.00"))

    def test_the_largest_break_reached_wins(self):
        self.agree("5.00")
        self.agree("4.00", min_quantity="100")
        self.agree("3.00", min_quantity="1000")
        self.assertEqual(self.draft_line("1500").unit_price, Decimal("3.00"))

    def test_an_expired_agreement_does_not_apply(self):
        self.agree("4.50", valid_to=datetime.date(2025, 12, 31))
        with self.assertRaisesMessage(ValidationError, "No agreed price"):
            self.draft_line("10")

    def test_a_future_agreement_does_not_apply_yet(self):
        self.agree("4.50", valid_from=datetime.date(2026, 6, 1))
        with self.assertRaisesMessage(ValidationError, "No agreed price"):
            self.draft_line("10")

    def test_another_vendor_s_price_does_not_apply(self):
        other = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        self.agree("4.50", vendor=other)

        with self.assertRaisesMessage(ValidationError, "No agreed price"):
            self.draft_line("10")

    def test_a_different_currency_does_not_apply(self):
        from apps.core.models import Currency

        eur = Currency.objects.create(code="EUR", name="Euro")
        VendorPrice.objects.create(
            vendor=self.vendor, item=self.item, currency=eur, unit_price=Decimal("4")
        )
        self.assertIsNone(
            resolve_purchase_price(self.item, self.vendor, quantity=Decimal("10"),
                                   currency=self.usd)
        )

    def test_an_inactive_price_is_ignored(self):
        self.agree("4.50", is_active=False)
        with self.assertRaisesMessage(ValidationError, "No agreed price"):
            self.draft_line("10")

    def test_a_vendor_must_hold_the_vendor_role(self):
        customer = Party.objects.create(code="C-9", name="Not a vendor")
        price = VendorPrice(
            vendor=customer, item=self.item, currency=self.usd, unit_price=Decimal("1")
        )
        with self.assertRaisesMessage(ValidationError, "does not have the Vendor role"):
            price.clean()


class LeadTimeAndPreferenceTests(VendorPriceTestCase):
    def test_the_agreed_lead_time_is_available(self):
        self.agree("4.50", lead_time_days=14)
        self.assertEqual(
            resolve_lead_time(self.item, self.vendor, quantity=Decimal("10")), 14
        )

    def test_a_flagged_preferred_vendor_wins(self):
        cheap = Party.objects.create(code="V-2", name="Cheap", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=cheap, role=PartyRole.VENDOR)
        self.agree("9.00", is_preferred=True)
        self.agree("2.00", vendor=cheap)

        self.assertEqual(preferred_vendor(self.item), self.vendor)

    def test_otherwise_the_cheapest_agreed_price_wins(self):
        cheap = Party.objects.create(code="V-2", name="Cheap", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=cheap, role=PartyRole.VENDOR)
        self.agree("9.00")
        self.agree("2.00", vendor=cheap)

        self.assertEqual(preferred_vendor(self.item), cheap)

    def test_no_agreement_means_no_preference(self):
        self.assertIsNone(preferred_vendor(self.item))


class PriceAgainstAgreementTests(VendorPriceTestCase):
    def test_it_reports_ordering_above_the_agreed_price(self):
        self.agree("4.00")
        line = self.draft_line("10", price="5.00")
        self.assertEqual(line.price_against_agreement(), Decimal("1.00"))

    def test_nothing_agreed_means_nothing_to_compare(self):
        self.assertIsNone(self.draft_line("10", price="5").price_against_agreement())


class SpendApprovalTests(VendorPriceTestCase):
    """A sales order gives away margin; a purchase order spends cash. The
    side that had no threshold at all was the second one."""

    def setUp(self):
        super().setUp()
        self.approver = get_user_model().objects.create_user(
            username="controller", password="x"
        )

    def order_of(self, quantity="10", price="5"):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
        )
        return order

    def test_nothing_is_enforced_until_a_policy_exists(self):
        order = self.order_of("10000", "100")
        order.confirm()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)

    def test_a_large_order_needs_approval(self):
        PurchaseApprovalPolicy.objects.create(
            code="STD", name="Standard", max_order_value=Decimal("1000")
        )
        order = self.order_of("1000", "5")

        self.assertEqual(order.approval_status(), ApprovalStatus.PENDING)
        with self.assertRaisesMessage(ValidationError, "needs approval"):
            order.confirm()

    def test_approving_lets_it_through_and_records_who(self):
        PurchaseApprovalPolicy.objects.create(
            code="STD", name="Standard", max_order_value=Decimal("1000")
        )
        order = self.order_of("1000", "5")

        order.approve(by=self.approver, note="Annual stock buy")
        order.confirm()

        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        self.assertEqual(order.approved_by, self.approver)

    def test_one_big_line_inside_an_ordinary_order_is_caught(self):
        PurchaseApprovalPolicy.objects.create(
            code="STD", name="Standard", max_line_value=Decimal("500")
        )
        order = self.order_of("1000", "5")
        self.assertIn("line limit", order.approval_reasons()[0])

    def test_a_hand_typed_price_can_require_approval(self):
        PurchaseApprovalPolicy.objects.create(
            code="STD", name="Standard", require_approval_without_vendor_price=True
        )
        order = self.order_of("10", "5")
        self.assertIn("priced by hand", order.approval_reasons()[0])

    def test_an_agreed_price_does_not(self):
        self.agree("5.00")
        PurchaseApprovalPolicy.objects.create(
            code="STD", name="Standard", require_approval_without_vendor_price=True
        )
        order = self.order_of("10", "5")
        self.assertEqual(order.approval_reasons(), [])

    def test_re_pricing_after_approval_drops_it(self):
        PurchaseApprovalPolicy.objects.create(
            code="STD", name="Standard", max_order_value=Decimal("1000")
        )
        order = self.order_of("1000", "5")
        order.approve(by=self.approver)

        line = order.lines.get()
        line.quantity = Decimal("2000")
        line.save()

        order.refresh_from_db()
        self.assertIsNone(order.approved_at)

    def test_approving_a_compliant_order_is_refused(self):
        PurchaseApprovalPolicy.objects.create(
            code="STD", name="Standard", max_order_value=Decimal("100000")
        )
        order = self.order_of("10", "5")
        with self.assertRaisesMessage(ValidationError, "breaches no policy"):
            order.approve(by=self.approver)

    def test_a_cancelled_order_cannot_be_approved(self):
        PurchaseApprovalPolicy.objects.create(
            code="STD", name="Standard", max_order_value=Decimal("1000")
        )
        order = self.order_of("1000", "5")
        order.cancel()
        with self.assertRaisesMessage(ValidationError, "cancelled order cannot be approved"):
            order.approve(by=self.approver)
