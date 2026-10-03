"""
Who may sign off up to what.

A policy with thresholds but no tiers asks "does this need approval" and
never "from whom", so one approve() served a team leader and the board
alike. The amount that triggers a second pair of eyes and the seniority
of that pair are different questions, and only the first was asked.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError

from .models import (
    ApprovalTier,
    Bill,
    PurchaseApprovalPolicy,
    PurchaseOrder,
    PurchaseOrderLine,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class TierTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.policy = PurchaseApprovalPolicy.objects.create(
            code="STD", name="Standard", max_order_value=Decimal("1000")
        )
        self.managers = Group.objects.create(name="Managers")
        self.directors = Group.objects.create(name="Directors")
        self.manager = self.user("mira", self.managers)
        self.director = self.user("dana", self.directors)
        self.clerk = self.user("chris")

    def user(self, name, *groups):
        user = get_user_model().objects.create_user(username=name, password="x")
        for group in groups:
            user.groups.add(group)
        return user

    def tiers(self):
        ApprovalTier.objects.create(
            policy=self.policy, group=self.managers, up_to_amount=Decimal("5000")
        )
        ApprovalTier.objects.create(
            policy=self.policy, group=self.directors, up_to_amount=None
        )

    def order_of(self, quantity, price="1"):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
        )
        return order


class TierAuthorityTests(TierTestCase):
    def test_no_tiers_means_anyone_may_approve(self):
        """Switching tiers on is a deliberate act, not something that
        silently locks every buyer out the day a policy is saved."""
        order = self.order_of("4000")
        order.approve(by=self.clerk)
        self.assertIsNotNone(order.approved_at)

    def test_a_manager_may_approve_within_their_limit(self):
        self.tiers()
        order = self.order_of("4000")

        order.approve(by=self.manager)

        self.assertEqual(order.approved_by, self.manager)

    def test_a_manager_may_not_approve_beyond_it(self):
        self.tiers()
        order = self.order_of("9000")

        with self.assertRaisesMessage(ValidationError, "cannot approve"):
            order.approve(by=self.manager)

    def test_a_director_may_approve_anything(self):
        self.tiers()
        order = self.order_of("500000")
        order.approve(by=self.director)
        self.assertIsNotNone(order.approved_at)

    def test_someone_in_no_tier_may_approve_nothing(self):
        self.tiers()
        order = self.order_of("2000")
        with self.assertRaisesMessage(ValidationError, "cannot approve"):
            order.approve(by=self.clerk)

    def test_an_unnamed_approver_is_refused_once_tiers_exist(self):
        """An approval with no name on it cannot be checked against a
        limit, and an unchecked approval is the thing tiers exist to
        prevent."""
        self.tiers()
        order = self.order_of("2000")
        with self.assertRaisesMessage(ValidationError, "needs a named approver"):
            order.approve()

    def test_a_superuser_is_not_blocked(self):
        self.tiers()
        root = get_user_model().objects.create_superuser(
            username="root", password="x", email=""
        )
        order = self.order_of("500000")
        order.approve(by=root)
        self.assertEqual(order.approved_by, root)

    def test_the_error_names_who_can_sign(self):
        self.tiers()
        order = self.order_of("9000")
        try:
            order.approve(by=self.manager)
        except ValidationError as exc:
            self.assertIn("Directors", exc.messages[0])
        else:
            self.fail("expected a refusal")

    def test_the_limit_is_read_against_the_order_total(self):
        self.tiers()
        within = self.order_of("5000")
        within.approve(by=self.manager)

        beyond = self.order_of("5001")
        with self.assertRaisesMessage(ValidationError, "cannot approve"):
            beyond.approve(by=self.manager)

    def test_a_group_appears_once_per_policy(self):
        self.tiers()
        with self.assertRaises(Exception):
            ApprovalTier.objects.create(
                policy=self.policy, group=self.managers, up_to_amount=Decimal("99")
            )


class CrossOrderPrepaymentTests(PurchasingLifecycleTestCase):
    """
    A prepayment is the vendor holding the company's money, not the
    order's. Applying it to a different order of the same vendor already
    worked — this records that it is deliberate, because the obvious
    reading of "prepayment on an order" is that it is stuck there.
    """

    def setUp(self):
        super().setUp()
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company

        self.prepaid = Account.objects.create(
            code="1400", name="Vendor Prepayments", account_type=AccountType.ASSET
        )
        company = Company.get()
        company.vendor_prepayment_account = self.prepaid
        company.default_purchase_expense_account = self.expense
        company.save()

    def test_a_prepayment_can_settle_another_order_for_the_same_vendor(self):
        first = self.make_order("10", "5")
        prepayment = first.create_prepayment_bill(self.payable, amount=Decimal("30"))
        prepayment.post()

        second = self.make_order("10", "5")
        self.receive(second, "10")
        bill = second.create_bill(self.payable)
        bill.post()

        bill.apply_prepayment(prepayment)

        self.assertEqual(bill.amount_due(), Decimal("20"))
        self.assertEqual(prepayment.prepayment_unapplied(), Decimal("0"))

    def test_it_still_cannot_cross_vendors(self):
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        first = self.make_order("10", "5")
        prepayment = first.create_prepayment_bill(self.payable, amount=Decimal("30"))
        prepayment.post()

        other = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        self.vendor = other
        second = self.make_order("10", "5")
        self.receive(second, "10")
        bill = second.create_bill(self.payable)
        bill.post()

        with self.assertRaisesMessage(ValidationError, "different vendor"):
            bill.apply_prepayment(prepayment)
