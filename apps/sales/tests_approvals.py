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

    def test_somebody_who_has_left_approves_nothing(self):
        """O300: the controller left on 1 June; in October their login decides no order's breach."""
        from apps.core.models import Party, PartyRole, PartyRoleAssignment
        from apps.hr.models import Employee

        party = Party.objects.create(code="P-CTRL", name="Controller")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        person = Employee.objects.create(party=party, employee_number="E-CTRL", hire_date=datetime.date(2020, 1, 1),
                                         user=self.approver)
        Employee.objects.filter(pk=person.pk).update(termination_date=datetime.date(2026, 6, 1),
                                                     employment_status="terminated")
        order = self.draft(discount="40")
        with self.assertRaisesMessage(ValidationError, "has left (leaving date 01-06-2026)"):
            order.approve(by=self.approver)
        self.assertIsNone(order.approved_at)

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

    def test_a_confirmed_order_is_not_raised_past_the_limit(self):
        # Confirmed at 600 against 1,000: raised to 6,000 it would be six
        # times over, and nothing had asked.
        from django.core.exceptions import ValidationError

        from .models import committed_balance

        CustomerProfile.objects.create(party=self.customer, credit_limit=Decimal("1000"))
        order = self.draft("6", "100")
        order.confirm()
        line = order.lines.get()
        line.quantity = Decimal("60")
        with self.assertRaisesMessage(ValidationError, "would be at 6000.00 against a credit limit of 1000.00"):
            line.save()
        self.assertEqual(committed_balance(self.customer), Decimal("600.00"))
        line.quantity = Decimal("9")
        line.save()
        added = SalesOrderLine(order=order, item=self.item, uom=self.uom, quantity=Decimal("2"),
                               unit_price=Decimal("100"), revenue_account=self.revenue)
        with self.assertRaisesMessage(ValidationError, "would be at 1100.00"):
            added.save()
        self.assertEqual(committed_balance(self.customer), Decimal("900.00"))

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


class NobodyApprovesTheirOwnOrderTests(ApprovalTestCase):
    """
    O85: approvals.py promises a second pair of eyes, and nothing asked
    whose: an AR Manager approved their own order at 40% against a 15%
    policy. Whoever made or changed the order, or wrote the quote it was
    accepted from, does not approve it.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=Decimal("15"))
        self.manager = get_user_model().objects.create_user("ar")
        self.manager.groups.add(Group.objects.get(name="AR Manager"))
        self.rep = get_user_model().objects.create_user("rep")
        self.client_ = APIClient()
        self.client_.force_authenticate(self.manager)

    def approve(self, order):
        response = self.client_.post(f"/api/sales/sales-orders/{order.pk}/approve/", {}, format="json")
        order.refresh_from_db()
        return response.status_code, order.approval_status()

    def test_an_ar_manager_does_not_approve_the_order_they_raised(self):
        order = self.draft(discount="40")
        order.created_by = self.manager
        order.save(update_fields=["created_by"])
        self.assertEqual(self.approve(order), (400, ApprovalStatus.PENDING))

    def test_nor_one_whose_line_they_changed(self):
        order = self.draft(discount="40")
        order.created_by = self.rep
        order.save(update_fields=["created_by"])
        line = order.lines.get()
        changed = self.client_.patch(f"/api/sales/sales-order-lines/{line.pk}/", {"discount_percent": "35"},
                                     format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(self.approve(order), (400, ApprovalStatus.PENDING))

    def test_the_reps_order_is_the_managers_to_approve(self):
        order = self.draft(discount="40")
        order.created_by = self.rep
        order.save(update_fields=["created_by"])
        self.assertEqual(self.approve(order), (200, ApprovalStatus.APPROVED))
        self.assertEqual(order.approved_by, self.manager)

    def test_the_quote_writer_does_not_approve_it_on_acceptance(self):
        quotation = QuotationApprovalTests.quote(self)  # the same 40% quote
        quotation.created_by = self.manager
        quotation.save(update_fields=["created_by"])
        with self.assertRaisesMessage(ValidationError, "somebody else approves it"):
            quotation.accept(approve_as=self.manager)
        quotation.refresh_from_db()
        self.assertIsNone(quotation.sales_order)

    def test_a_reps_quote_is_accepted_and_approved_by_the_manager(self):
        quotation = QuotationApprovalTests.quote(self)  # the same 40% quote
        quotation.created_by = self.rep
        quotation.save(update_fields=["created_by"])
        order = quotation.accept(approve_as=self.manager)
        self.assertEqual((order.status, order.approved_by), (OrderStatus.CONFIRMED, self.manager))


class SelfApprovalThroughARevisionTests(ApprovalTestCase):
    """
    O143: a revision copied the quote's lines in code, unstamped, and
    authors() did not follow revision_of: the manager who wrote a 40%
    quote was refused its order, revised it, and approved the revision's.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=Decimal("15"))
        self.manager = get_user_model().objects.create_user("ar")
        self.manager.groups.add(Group.objects.get(name="AR Manager"))
        self.rep = get_user_model().objects.create_user("rep")
        self.rep.groups.add(Group.objects.get(name="Sales Rep"))
        from .tests_base import carries_every_customer

        carries_every_customer(self.rep)

    def client_for(self, user):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user)
        return client

    def sent_quote(self, writer):
        from .models import Quotation, QuotationLine, QuotationStatus

        quotation = Quotation.objects.create(customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
                                             valid_until=datetime.date(2027, 4, 1), currency=self.usd,
                                             created_by=writer, updated_by=writer)
        QuotationLine.objects.create(quotation=quotation, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                     unit_price=Decimal("100"), discount_percent=Decimal("40"),
                                     revenue_account=self.revenue, created_by=writer, updated_by=writer)
        Quotation.objects.filter(pk=quotation.pk).update(status=QuotationStatus.SENT)
        return quotation

    def revised(self, quotation, by):
        from .models import Quotation, QuotationStatus

        revised = self.client_for(by).post(f"/api/sales/quotations/{quotation.pk}/revise/", {}, format="json")
        self.assertEqual(revised.status_code, 200, revised.content)
        Quotation.objects.filter(pk=revised.json()["id"]).update(status=QuotationStatus.SENT)
        return revised.json()["id"]

    def accept(self, pk, by):
        return self.client_for(by).post(f"/api/sales/quotations/{pk}/accept/", {"approve": True}, format="json")

    def test_the_manager_who_wrote_the_quote_does_not_approve_it_through_its_revision(self):
        quotation = self.sent_quote(self.manager)
        self.assertEqual(self.accept(quotation.pk, self.manager).status_code, 400)
        accepted = self.accept(self.revised(quotation, self.manager), self.manager)
        self.assertEqual(accepted.status_code, 400, accepted.content)

    def test_nor_the_manager_who_revised_a_reps_quote(self):
        quotation = self.sent_quote(self.rep)
        accepted = self.accept(self.revised(quotation, self.manager), self.manager)
        self.assertEqual(accepted.status_code, 400, accepted.content)

    def test_a_reps_revised_quote_is_the_managers_to_approve(self):
        quotation = self.sent_quote(self.rep)
        accepted = self.accept(self.revised(quotation, self.rep), self.manager)
        self.assertEqual((accepted.status_code, accepted.json()["status"]), (200, "confirmed"), accepted.content)


class AForeignOrderIsWeighedInTheBaseTests(ApprovalTestCase):
    """O293: the policy's limit and the item's cost are base figures; a euro order is weighed at its own rate, 90."""

    def eur_order(self, quantity, price):
        from apps.core.models import Currency, ExchangeRate
        from .models import SalesOrder

        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("90"), valid_from=datetime.date(2026, 1, 1))
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
                                      unit_price=Decimal(price), revenue_account=self.revenue)
        return order

    def test_a_euro_order_worth_4_5_million_base_asks_the_1_million_limit(self):
        # 10 x 5,000 EUR at 90 = 45,00,000 base against a 10,00,000 limit.
        from .models import ApprovalPolicy

        ApprovalPolicy.objects.create(code="P", name="Limit", max_order_value=Decimal("1000000"))
        order = self.eur_order("10", "5000")
        self.assertIn("The order is 4500000.00 in the base currency", " ".join(order.approval_reasons()))
        with self.assertRaises(ValidationError):
            order.confirm()

    def test_a_euro_sale_at_a_95_percent_margin_is_not_held_by_a_20_percent_floor(self):
        # Cost 4 base a unit; 1 EUR a unit at 90 is 90 base: (900 - 40) / 900 = 95.56%, not 1 - 4 = -300%.
        from .models import ApprovalPolicy

        ApprovalPolicy.objects.create(code="P", name="Floor", min_margin_percent=Decimal("20"))
        order = self.eur_order("10", "1")
        self.assertEqual(order.margin_percent(), Decimal("95.56"))
        order.confirm()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)

    def test_an_order_with_no_rate_on_its_date_is_refused_at_confirmation_in_words(self):
        from apps.core.models import Currency, ExchangeRate
        from .models import SalesOrder

        gbp = Currency.objects.create(code="GBP", name="Pound")
        ExchangeRate.objects.create(currency=gbp, rate=Decimal("105"), valid_from=datetime.date(2026, 4, 1))
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=gbp)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                      unit_price=Decimal("10"), revenue_account=self.revenue)
        self.assertIn("No exchange rate for GBP on 01-03-2026", " ".join(order.approval_reasons()))
        with self.assertRaisesMessage(ValidationError, "No exchange rate for GBP on 01-03-2026"):
            order.confirm()

    def test_every_confirmed_order_is_weighed_at_its_own_date(self):
        # O342: 10 x 10 EUR confirmed on 1 March at 90 is 9,000; April's 120 would read it 12,000.
        # A second order of 1 x 5 EUR on 2 April at 120 is 600: 9,600 against 10,000, not 12,600.
        from apps.core.models import ExchangeRate
        from .models import CustomerProfile, SalesOrder, committed_in_base

        CustomerProfile.objects.update_or_create(party=self.customer, defaults={"credit_limit": Decimal("10000")})
        first = self.eur_order("10", "10")
        first.confirm()
        ExchangeRate.objects.create(currency=first.currency, rate=Decimal("120"), valid_from=datetime.date(2026, 4, 1))
        self.assertEqual(committed_in_base(self.customer), Decimal("9000.00"))
        second = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 4, 2),
                                           currency=first.currency)
        SalesOrderLine.objects.create(order=second, item=self.item, uom=self.uom, quantity=Decimal("1"),
                                      unit_price=Decimal("5"), revenue_account=self.revenue)
        self.assertIsNone(second.credit_limit_breach())
        second.confirm()
        self.assertEqual(committed_in_base(self.customer), Decimal("9600.00"))

    def test_the_credit_limit_weighs_the_order_at_its_own_date(self):
        # O334: 10 x 10 EUR on 1 March at 90 is 9,000 against a 10,000 limit; at April's 120 it would read 12,000.
        from apps.core.models import ExchangeRate
        from .models import CustomerProfile

        CustomerProfile.objects.update_or_create(party=self.customer, defaults={"credit_limit": Decimal("10000")})
        order = self.eur_order("10", "10")
        ExchangeRate.objects.create(currency=order.currency, rate=Decimal("120"), valid_from=datetime.date(2026, 4, 1))
        self.assertIsNone(order.credit_limit_breach())
        self.assertEqual(order.total_in_base(), Decimal("9000.00"))


from apps.core.tests_roles import RolesTestCase  # noqa: E402


class ARepIsNotToldTheMarginTests(RolesTestCase, ApprovalTestCase):
    """O299: the margin is cost; the approval panel and a refused confirm tell it only to someone who may approve."""

    def setUp(self):
        ApprovalTestCase.setUp(self)
        RolesTestCase.setUp(self)
        from .models import ApprovalPolicy

        ApprovalPolicy.objects.create(code="P", name="Floor", min_margin_percent=Decimal("20"))
        # 10 at 1 against a cost of 4 a unit: (10 - 40) / 10 = -300.00%.
        self.order = self.draft(quantity="10", price="1")

    def client_as(self, role):
        from django.contrib.auth.models import Group
        from rest_framework.test import APIClient

        from .tests_base import carries_every_customer

        user = get_user_model().objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        carries_every_customer(user)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_a_rep_reads_that_it_is_under_the_floor_not_by_how_much(self):
        rep = self.client_as("Sales Rep")
        reply = rep.get(f"/api/sales/sales-orders/{self.order.pk}/approval/")
        self.assertEqual(reply.status_code, 200, reply.content)
        self.assertIsNone(reply.json()["margin_percent"])
        self.assertEqual(reply.json()["reasons"], ["The gross margin is below the 20.00% floor."])
        refused = rep.post(f"/api/sales/sales-orders/{self.order.pk}/confirm/", {}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertNotIn("-300", refused.content.decode())
        self.assertIn("below the 20.00% floor", refused.content.decode())

    def test_the_approver_is_told_the_figure(self):
        approver = self.client_as("AR Manager")
        reply = approver.get(f"/api/sales/sales-orders/{self.order.pk}/approval/")
        self.assertEqual(reply.status_code, 200, reply.content)
        self.assertEqual(reply.json()["margin_percent"], "-300.00")
        self.assertEqual(reply.json()["reasons"], ["Gross margin is -300.00%, below the 20.00% floor."])


from django.test import TransactionTestCase, tag  # noqa: E402


@tag("migration")
class ApprovedFiguresPutInTheBaseMigrationTests(TransactionTestCase):
    """O337: an approval kept before O293 in the order's currency is put in the base at the order's rate."""

    before = [("sales", "0077_one_opportunity_an_order")]
    after = [("sales", "0078_approved_figures_in_base")]

    def test_a_euro_order_is_put_in_the_base_and_the_others_are_left(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.utils import timezone

        from apps.core.models import Currency, ExchangeRate, Party, PartyRole, PartyRoleAssignment
        from .models import SalesOrder

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        try:
            inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
            eur = Currency.objects.create(code="EUR", name="Euro")
            gbp = Currency.objects.create(code="GBP", name="Pound")
            ExchangeRate.objects.create(currency=eur, rate=Decimal("90"), valid_from=datetime.date(2026, 1, 1))
            ExchangeRate.objects.create(currency=gbp, rate=Decimal("105"), valid_from=datetime.date(2026, 4, 1))
            customer = Party.objects.create(code="C-MIG", name="Bengal Cement")
            PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
            kept = {"discounts": {"1": "5.00"}, "total": "50000.00", "margin": "-300.00"}
            orders = {}
            for name, currency in (("eur", eur), ("inr", inr), ("gbp", gbp)):
                orders[name] = SalesOrder.objects.create(customer=customer, order_date=datetime.date(2026, 3, 1),
                                                         currency=currency)
            SalesOrder.objects.update(approved_at=timezone.now(), approved_figures=kept)
            MigrationExecutor(connection).migrate(self.after)
            figures = {name: SalesOrder.objects.get(pk=order.pk).approved_figures for name, order in orders.items()}
            # 50,000 EUR at 90; the margin 100 - (100 + 300) / 90 = 95.56 (calc_fix_trade2/o337.py).
            self.assertEqual(figures["eur"], {"discounts": {"1": "5.00"}, "total": "4500000.00", "margin": "95.56"})
            self.assertEqual(figures["inr"], kept)
            self.assertEqual(figures["gbp"], kept)   # no rate on 1 March: left as it is
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
