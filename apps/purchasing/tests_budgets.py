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

    def test_no_budget_on_the_account_means_no_constraint(self):
        self.budget.delete()
        order = self.order_of("100000", "20", confirm=False)
        self.assertEqual(order.approval_reasons(), [])
