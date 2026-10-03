"""
Reorder rules.

The loop that connects purchasing to the rest of the system. Without it,
preferred_vendor() and the agreed lead_time_days were each read in
exactly one place and did nothing the rest of the time — configuration
that looked like a feature.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse
from apps.sales.models import SalesOrder, SalesOrderLine

from .models import (
    PurchaseOrder,
    PurchaseOrderLine,
    ReorderRule,
    RequisitionStatus,
    VendorPrice,
    raise_reorder_requisition,
    reorder_suggestions,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class ReorderTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.employee = Party.objects.create(code="E-1", name="Dana")
        PartyRoleAssignment.objects.create(party=self.employee, role=PartyRole.EMPLOYEE)
        VendorPrice.objects.create(
            vendor=self.vendor, item=self.item, currency=self.usd,
            unit_price=Decimal("4"), lead_time_days=14, is_preferred=True,
        )

    def stock(self, quantity, warehouse=None):
        StockMovement.objects.create(
            item=self.item, warehouse=warehouse or self.warehouse,
            movement_type=MovementType.RECEIPT, uom=self.item.uom, quantity=Decimal(quantity),
            unit_cost=Decimal("4"), occurred_at=timezone.now(),
        )

    def rule(self, minimum="20", target="100", **kwargs):
        return ReorderRule.objects.create(
            item=self.item, warehouse=self.warehouse,
            minimum=Decimal(minimum), target=Decimal(target), **kwargs
        )


class ProjectionTests(ReorderTestCase):
    def test_plenty_in_stock_suggests_nothing(self):
        self.stock("200")
        self.rule("20", "100")
        self.assertEqual(reorder_suggestions(), [])

    def test_falling_to_the_minimum_suggests_topping_up(self):
        self.stock("15")
        rule = self.rule("20", "100")

        rows = reorder_suggestions()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], Decimal("85"))
        self.assertEqual(rows[0]["projected"], Decimal("15"))

    def test_hitting_the_minimum_exactly_counts(self):
        self.stock("20")
        self.rule("20", "100")
        self.assertEqual(reorder_suggestions()[0]["quantity"], Decimal("80"))

    def test_stock_already_on_order_is_not_ordered_again(self):
        """Ordering again for stock already on its way is how one shortage
        becomes two months of excess."""
        self.stock("15")
        rule = self.rule("20", "100")
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("200"), unit_price=Decimal("4"),
        )
        order.confirm()

        self.assertEqual(rule.on_order(), Decimal("200"))
        self.assertEqual(reorder_suggestions(), [])

    def test_a_received_order_stops_counting_as_inbound(self):
        self.stock("15")
        rule = self.rule("20", "100")
        order = self.make_order("200", "4")
        self.receive(order, "200")

        self.assertEqual(rule.on_order(), Decimal("0"))

    def test_stock_promised_to_customers_does_not_count_as_available(self):
        """On hand alone would reorder for stock that is spoken for."""
        self.stock("150")
        rule = self.rule("20", "100")
        revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        sale = SalesOrder.objects.create(
            customer=customer, order_date=datetime.date(2026, 1, 1), currency=self.usd
        )
        SalesOrderLine.objects.create(
            order=sale, item=self.item, uom=self.uom, quantity=Decimal("140"),
            unit_price=Decimal("9"), revenue_account=revenue,
        )
        sale.confirm()

        self.assertEqual(rule.committed(), Decimal("140"))
        self.assertEqual(rule.projected(), Decimal("10"))
        self.assertEqual(reorder_suggestions()[0]["quantity"], Decimal("90"))

    def test_stock_sitting_in_quarantine_does_not_cover_a_shortage(self):
        """It is owned, but it cannot be shipped and it may yet go back."""
        quarantine = Warehouse.objects.create(
            code="QA", name="Bay", is_quarantine=True
        )
        rule = self.rule("20", "100")
        self.stock("15")
        self.stock("200", warehouse=quarantine)

        self.assertEqual(rule.projected(), Decimal("15"))
        self.assertEqual(reorder_suggestions(warehouse=self.warehouse)[0]["quantity"],
                         Decimal("85"))


class RoundingTests(ReorderTestCase):
    def test_orders_round_up_to_a_whole_case(self):
        self.stock("15")
        self.rule("20", "100", multiple_of=Decimal("24"))

        # 85 short, rounded up to four cases of 24.
        self.assertEqual(reorder_suggestions()[0]["quantity"], Decimal("96"))

    def test_an_exact_multiple_is_left_alone(self):
        self.stock("4")
        self.rule("20", "100", multiple_of=Decimal("24"))
        self.assertEqual(reorder_suggestions()[0]["quantity"], Decimal("96"))


class VendorChoiceTests(ReorderTestCase):
    def test_it_suggests_the_preferred_vendor_and_their_terms(self):
        """This is the whole reason preferred_vendor and lead_time exist."""
        self.stock("15")
        self.rule("20", "100")

        row = reorder_suggestions()[0]

        self.assertEqual(row["vendor"], self.vendor)
        self.assertEqual(row["unit_price"], Decimal("4"))
        self.assertEqual(row["lead_time_days"], 14)

    def test_a_rule_can_name_its_own_vendor(self):
        other = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        self.stock("15")
        self.rule("20", "100", vendor=other)

        self.assertEqual(reorder_suggestions()[0]["vendor"], other)

    def test_no_agreed_price_still_suggests_the_quantity(self):
        VendorPrice.objects.all().delete()
        self.stock("15")
        self.rule("20", "100")

        row = reorder_suggestions()[0]
        self.assertEqual(row["quantity"], Decimal("85"))
        self.assertIsNone(row["vendor"])
        self.assertIsNone(row["unit_price"])


class RuleValidationTests(ReorderTestCase):
    def test_a_target_below_the_minimum_is_refused(self):
        rule = ReorderRule(
            item=self.item, warehouse=self.warehouse,
            minimum=Decimal("100"), target=Decimal("20"),
        )
        with self.assertRaisesMessage(ValidationError, "cannot be below the minimum"):
            rule.clean()

    def test_a_named_vendor_must_be_one(self):
        rule = ReorderRule(
            item=self.item, warehouse=self.warehouse,
            minimum=Decimal("20"), target=Decimal("100"), vendor=self.employee,
        )
        with self.assertRaisesMessage(ValidationError, "does not have the Vendor role"):
            rule.clean()

    def test_one_rule_per_item_and_warehouse(self):
        self.rule()
        with self.assertRaises(Exception):
            self.rule()

    def test_an_inactive_rule_suggests_nothing(self):
        self.stock("1")
        self.rule("20", "100", is_active=False)
        self.assertEqual(reorder_suggestions(), [])


class RequisitionFromReorderTests(ReorderTestCase):
    def test_it_raises_a_requisition_somebody_has_to_approve(self):
        """A rule firing on stale data should cost a conversation, not a
        delivery."""
        self.stock("15")
        self.rule("20", "100")

        requisition = raise_reorder_requisition(self.employee)

        self.assertEqual(requisition.status, RequisitionStatus.DRAFT)
        line = requisition.lines.get()
        self.assertEqual(line.quantity, Decimal("85"))
        self.assertEqual(line.suggested_vendor, self.vendor)
        self.assertEqual(line.estimated_price, Decimal("4"))

    def test_the_line_says_why_it_was_raised(self):
        self.stock("15")
        self.rule("20", "100")
        requisition = raise_reorder_requisition(self.employee)
        self.assertIn("Projected 15", requisition.lines.get().notes)

    def test_nothing_short_raises_nothing(self):
        self.stock("500")
        self.rule("20", "100")
        with self.assertRaisesMessage(ValidationError, "reorder point"):
            raise_reorder_requisition(self.employee)

    def test_it_can_be_scoped_to_one_warehouse(self):
        second = Warehouse.objects.create(code="W2", name="South")
        self.stock("15")
        self.rule("20", "100")
        ReorderRule.objects.create(
            item=self.item, warehouse=second,
            minimum=Decimal("5"), target=Decimal("50"),
        )

        self.assertEqual(len(reorder_suggestions()), 2)
        self.assertEqual(len(reorder_suggestions(warehouse=second)), 1)
