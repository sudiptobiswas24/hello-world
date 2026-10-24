"""
Budgets and commitment accounting.

The point is commitment, not the limit. A ledger tells you what has been
spent; by then the money is gone. A budget that counts only posted bills
reports a department as healthy right up to the month a year of purchase
orders lands on it at once.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.core.approvals import ApprovalStatus
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment

from .models import (
    Budget,
    OrderStatus,
    PurchaseApprovalPolicy,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    PurchaseRequisitionLine,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class BudgetTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()
        self.budget = Budget.objects.create(
            code="OPS-26", name="Operations 2026", account=self.expense,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
            amount=Decimal("10000"),
        )
        self.employee = Party.objects.create(code="E-1", name="Dana")
        PartyRoleAssignment.objects.create(party=self.employee, role=PartyRole.EMPLOYEE)

    def order_of(self, quantity, price, confirm=True, on=datetime.date(2026, 3, 1)):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=on
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            expense_account=self.expense,
        )
        if confirm:
            order.confirm()
        return order


class CommitmentTests(BudgetTestCase):
    def test_a_fresh_budget_is_wholly_available(self):
        self.assertEqual(self.budget.available(), Decimal("10000"))

    def test_a_confirmed_order_commits_the_money(self):
        """The moment the company loses the ability to change its mind for
        free — not the moment the bill arrives."""
        self.order_of("100", "20")

        self.assertEqual(self.budget.committed(), Decimal("2000"))
        self.assertEqual(self.budget.spent(), Decimal("0"))
        self.assertEqual(self.budget.available(), Decimal("8000"))

    def test_a_draft_order_commits_nothing(self):
        self.order_of("100", "20", confirm=False)
        self.assertEqual(self.budget.committed(), Decimal("0"))

    def test_a_cancelled_order_releases_it(self):
        order = self.order_of("100", "20", confirm=False)
        order.cancel()
        self.assertEqual(self.budget.committed(), Decimal("0"))

    def test_billing_moves_commitment_into_spend(self):
        order = self.order_of("100", "20")
        self.receive(order, "100")
        bill = order.create_bill(self.payable)
        bill.bill_date = datetime.date(2026, 3, 10)
        bill.post()

        self.assertEqual(self.budget.committed(), Decimal("0"))
        self.assertEqual(self.budget.spent(), Decimal("2000"))
        self.assertEqual(self.budget.available(), Decimal("8000"))

    def test_a_part_billed_order_splits_between_the_two(self):
        order = self.order_of("100", "20")
        self.receive(order, "40")
        bill = order.create_bill(self.payable)
        bill.bill_date = datetime.date(2026, 3, 10)
        bill.post()

        self.assertEqual(self.budget.spent(), Decimal("800"))
        self.assertEqual(self.budget.committed(), Decimal("1200"))
        self.assertEqual(self.budget.available(), Decimal("8000"))

    def test_a_line_closed_short_commits_only_what_it_will_still_bill(self):
        # Closed short at 40 of 100, the order went on committing the 60 that would never
        # come: 2,000 until billed, then 1,200 for ever.
        order = self.order_of("100", "20")
        self.receive(order, "40")
        order.lines.get().close_short("Vendor discontinued it")
        self.assertEqual(self.budget.committed(), Decimal("800"))
        bill = order.create_bill(self.payable)
        bill.bill_date = datetime.date(2026, 3, 10)
        bill.post()
        self.assertEqual((self.budget.spent(), self.budget.committed()), (Decimal("800"), Decimal("0")))

    def test_an_approved_requisition_is_counted_before_it_is_ordered(self):
        requisition = PurchaseRequisition.objects.create(
            requested_by=self.employee, request_date=datetime.date(2026, 2, 1)
        )
        PurchaseRequisitionLine.objects.create(
            requisition=requisition, item=self.item, uom=self.uom,
            quantity=Decimal("100"), estimated_price=Decimal("5"),
            expense_account=self.expense,
        )
        requisition.submit()
        requisition.approve(by=get_user_model().objects.create_user("m", password="x"))

        self.assertEqual(self.budget.requested(), Decimal("500"))
        self.assertEqual(self.budget.available(), Decimal("9500"))

    def test_an_unapproved_requisition_is_not(self):
        requisition = PurchaseRequisition.objects.create(
            requested_by=self.employee, request_date=datetime.date(2026, 2, 1)
        )
        PurchaseRequisitionLine.objects.create(
            requisition=requisition, item=self.item, uom=self.uom,
            quantity=Decimal("100"), estimated_price=Decimal("5"),
            expense_account=self.expense,
        )
        requisition.submit()

        self.assertEqual(self.budget.requested(), Decimal("0"))


class BudgetScopeTests(BudgetTestCase):
    def test_another_account_does_not_touch_it(self):
        from apps.accounting.models import Account, AccountType

        other = Account.objects.create(
            code="5100", name="Marketing", account_type=AccountType.EXPENSE
        )
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 3, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("100"), unit_price=Decimal("20"),
            expense_account=other,
        )
        order.confirm()

        self.assertEqual(self.budget.committed(), Decimal("0"))

    def test_an_order_outside_the_period_does_not(self):
        self.order_of("100", "20", on=datetime.date(2027, 3, 1))
        self.assertEqual(self.budget.committed(), Decimal("0"))

    def test_it_cannot_end_before_it_starts(self):
        budget = Budget(
            code="BAD", name="Bad", account=self.expense,
            start_date=datetime.date(2026, 12, 31), end_date=datetime.date(2026, 1, 1),
            amount=Decimal("100"),
        )
        with self.assertRaisesMessage(ValidationError, "cannot end before it starts"):
            budget.clean()

    def test_lookup_finds_the_budget_covering_a_date(self):
        found = Budget.for_account(self.expense, datetime.date(2026, 6, 1))
        self.assertEqual(found, self.budget)
        self.assertIsNone(Budget.for_account(self.expense, datetime.date(2025, 6, 1)))

    def test_an_inactive_budget_is_not_found(self):
        self.budget.is_active = False
        self.budget.save()
        self.assertIsNone(Budget.for_account(self.expense, datetime.date(2026, 6, 1)))


class BudgetApprovalTests(BudgetTestCase):
    def setUp(self):
        super().setUp()
        PurchaseApprovalPolicy.objects.create(code="STD", name="Standard")
        self.approver = get_user_model().objects.create_user(username="boss", password="x")

    def test_an_order_within_budget_needs_no_approval(self):
        order = self.order_of("100", "20", confirm=False)
        self.assertEqual(order.approval_reasons(), [])

    def test_an_order_beyond_the_remaining_budget_needs_approval(self):
        self.order_of("400", "20")  # commits 8000 of 10000
        over = self.order_of("200", "20", confirm=False)  # wants 4000

        self.assertEqual(over.approval_status(), ApprovalStatus.PENDING)
        self.assertIn("left on budget OPS-26", over.approval_reasons()[0])

    def test_it_can_be_approved_over_budget_deliberately(self):
        self.order_of("400", "20")
        over = self.order_of("200", "20", confirm=False)

        over.approve(by=self.approver, note="Signed off by finance")
        over.confirm()

        self.assertEqual(over.status, "confirmed")

    def test_a_confirmed_order_is_not_raised_past_its_budget(self):
        # 2,000 of 10,000 confirmed; raised to 20,000 the budget would be
        # 10,000 over, and nothing had asked.
        order = self.order_of("100", "20")
        line = order.lines.get()
        line.quantity = Decimal("1000")
        with self.assertRaisesMessage(ValidationError, "Budget OPS-26 would be 10000.00 over"):
            line.save()
        self.assertEqual(self.budget.available(), Decimal("8000.00"))
        line.quantity = Decimal("500")
        line.save()
        self.assertEqual(self.budget.available(), Decimal("0.00"))

    def test_no_budget_on_the_account_means_no_constraint(self):
        self.budget.delete()
        order = self.order_of("100000", "20", confirm=False)
        self.assertEqual(order.approval_reasons(), [])


class AForeignOrderIsWeighedInTheBaseTests(BudgetTestCase):
    """O293: a budget and the policy's limits are base figures; a euro order is weighed at its own rate, 90."""

    def setUp(self):
        super().setUp()
        from apps.core.models import Currency, ExchangeRate

        self.eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=self.eur, rate=Decimal("90"), valid_from=datetime.date(2026, 1, 1))

    def eur_order(self, quantity, price, confirm=True):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=datetime.date(2026, 3, 1), currency=self.eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
                                         unit_price=Decimal(price), expense_account=self.expense)
        if confirm:
            order.confirm()
        return order

    def test_a_euro_order_commits_and_spends_the_budget_in_rupees(self):
        # 10 x 10 EUR at 90 = 9,000 committed of 10,000; 4 billed: 3,600 spent and 5,400 committed.
        order = self.eur_order("10", "10")
        self.assertEqual((self.budget.committed(), self.budget.available()), (Decimal("9000.00"), Decimal("1000.00")))
        self.receive(order, "4")
        bill = order.create_bill(self.payable)
        bill.bill_date = datetime.date(2026, 3, 10)
        bill.post()
        self.assertEqual((self.budget.spent(), self.budget.committed()), (Decimal("3600.00"), Decimal("5400.00")))
        self.assertEqual(self.budget.available(), Decimal("1000.00"))
        # 2 x 10 EUR more is 1,800 against the 1,000 left: it asks, where 20 EUR would have passed.
        PurchaseApprovalPolicy.objects.create(code="P", name="Budgets")  # the budget is asked under a policy
        more = self.eur_order("2", "10", confirm=False)
        self.assertTrue(any("1800.00 in the base currency" in reason for reason in more.approval_reasons()),
                        more.approval_reasons())

    def test_a_euro_order_worth_4_5_million_base_asks_the_1_million_limit(self):
        self.budget.delete()
        PurchaseApprovalPolicy.objects.create(code="P", name="Limit", max_order_value=Decimal("1000000"),
                                              max_line_value=Decimal("2000000"))
        order = self.eur_order("10", "5000", confirm=False)
        self.assertEqual(order.total_in_base(), Decimal("4500000.00"))
        reasons = order.approval_reasons()
        self.assertEqual(len(reasons), 2, reasons)
        self.assertIn("The order is 4500000.00 in the base currency", reasons[0])
        with self.assertRaises(ValidationError):
            order.confirm()

    def gbp_order(self):
        # Pounds with a rate only from 1 April: an order of 1 March has no base figure.
        from apps.core.models import Currency, ExchangeRate

        gbp = Currency.objects.create(code="GBP", name="Pound")
        ExchangeRate.objects.create(currency=gbp, rate=Decimal("105"), valid_from=datetime.date(2026, 4, 1))
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=datetime.date(2026, 3, 1), currency=gbp)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                         unit_price=Decimal("10"), expense_account=self.expense)
        return order

    def test_an_order_with_no_rate_on_its_date_is_refused_at_confirmation_in_words(self):
        order = self.gbp_order()
        with self.assertRaisesMessage(ValidationError, "No exchange rate for GBP on 01-03-2026"):
            order.confirm()
        self.assertEqual(PurchaseOrder.objects.get(pk=order.pk).status, OrderStatus.DRAFT)

    def test_one_order_with_no_rate_leaves_every_other_read_working_and_is_named(self):
        # Review of O293: one confirmed foreign order dated before its currency's first rate made every budget
        # read and every other order's approval_reasons raise. It is left out, and said.
        stuck = self.gbp_order()
        PurchaseOrder.objects.filter(pk=stuck.pk).update(status=OrderStatus.CONFIRMED)
        self.order_of("10", "100")
        PurchaseApprovalPolicy.objects.create(code="P", name="Budgets")
        self.assertEqual((self.budget.committed(), self.budget.available()), (Decimal("1000.00"), Decimal("9000.00")))
        (gap,) = self.budget.unweighed()
        self.assertIn("No exchange rate for GBP on 01-03-2026", gap)
        other = self.order_of("2", "100", confirm=False)
        self.assertEqual(other.approval_reasons(), [])
        self.assertIn("No exchange rate for GBP", " ".join(PurchaseOrder.objects.get(pk=stuck.pk).approval_reasons()))


from django.test import TransactionTestCase, tag  # noqa: E402


@tag("migration")
class ApprovedFiguresPutInTheBaseMigrationTests(TransactionTestCase):
    """O337: an approval kept before O293 in the order's currency is put in the base at the order's rate."""

    before = [("purchasing", "0080_allocation_keeps_its_base")]
    after = [("purchasing", "0081_approved_figures_in_base")]

    def test_a_euro_order_is_put_in_the_base_and_the_others_are_left(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.utils import timezone

        from apps.core.models import Currency, ExchangeRate
        from .models import PurchaseOrder

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        try:
            inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
            eur = Currency.objects.create(code="EUR", name="Euro")
            ExchangeRate.objects.create(currency=eur, rate=Decimal("90"), valid_from=datetime.date(2026, 1, 1))
            vendor = Party.objects.create(code="V-MIG", name="Reliance Polymers")
            PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
            kept = {"total": "50000.00", "lines": {"7": "50000.00"}, "prices": {"7": "5000.00"}}
            eur_order = PurchaseOrder.objects.create(vendor=vendor, order_date=datetime.date(2026, 3, 1), currency=eur)
            inr_order = PurchaseOrder.objects.create(vendor=vendor, order_date=datetime.date(2026, 3, 1), currency=inr)
            named = PurchaseOrder.objects.create(vendor=vendor, order_date=datetime.date(2026, 3, 1), currency=eur)
            PurchaseOrder.objects.update(approved_at=timezone.now(), approved_figures=kept)
            in_base = {"total": "4500000.00", "lines": {"7": "4500000.00"}, "prices": {"7": "5000.00"},
                       "currency": "INR"}
            PurchaseOrder.objects.filter(pk=named.pk).update(approved_figures=in_base)
            MigrationExecutor(connection).migrate(self.after)
            # 50,000 EUR at 90, total and line; the price a unit stays in euros, as policy_figures() keeps it.
            self.assertEqual(PurchaseOrder.objects.get(pk=eur_order.pk).approved_figures, in_base)
            self.assertEqual(PurchaseOrder.objects.get(pk=inr_order.pk).approved_figures, {**kept, "currency": "INR"})
            # Named already: in the base, not converted twice.
            self.assertEqual(PurchaseOrder.objects.get(pk=named.pk).approved_figures, in_base)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
