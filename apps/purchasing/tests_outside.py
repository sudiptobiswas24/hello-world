"""
Paying a vendor for a step of a run, through the run.

Before this a vendor's coating was an ordinary service line: its
receipt posted nothing and its bill went straight to expense. The run
never saw the charge, its variance at close was short by all of it,
and the expense account carried manufacturing cost it had no business
holding.

The fixture's tape run is coated outside at 2.00 a kilo; the purchase
order agrees 2.10. Hand-checked: 1,000 kg back is 2,100 accrued, and a
bill at 2.10 clears exactly that.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import (
    Company,
    PaymentTerms,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)
from apps.inventory.models import Item, StockMovement
from apps.manufacturing.orders import ManufacturingSettings
from apps.manufacturing.routing import Routing, RoutingOperation
from apps.manufacturing.tests_orders import TODAY, RunTestCase

from .models import (
    Bill,
    BillLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
)


class OutsidePurchaseTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        acc = lambda code, name, kind: Account.objects.create(
            code=code, name=name, account_type=kind
        )
        self.grni = acc("2150", "GRNI", AccountType.LIABILITY)
        self.payable = acc("2000", "Payables", AccountType.LIABILITY)
        self.ppv = acc("5150", "Price variance", AccountType.EXPENSE)
        self.services = acc("5400", "Services bought", AccountType.EXPENSE)
        company = Company.get()
        company.grni_account = self.grni
        company.purchase_price_variance_account = self.ppv
        company.default_purchase_expense_account = self.services
        company.save()
        settings = ManufacturingSettings.get()
        settings.conversion_absorbed_account = acc(
            "5300", "Absorbed", AccountType.EXPENSE
        )
        settings.save()

        terms = PaymentTerms.objects.create(code="N30", name="Net 30", net_days=30)
        self.vendor = Party.objects.create(
            code="V-COAT", name="Coaters Ltd", default_currency=self.usd,
            payment_terms=terms,
        )
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.coating = Item.objects.create(
            sku="SVC-COAT", name="Coating service", uom=self.kg,
            track_inventory=False,
        )

        self.loom.capacity_per_hour = Decimal("180")
        self.loom.capacity_uom = self.kg
        self.loom.save()
        routing = Routing.objects.create(code="R-COAT", name="Coated tape")
        RoutingOperation.objects.create(
            routing=routing, sequence=10, name="Extrude", work_centre=self.loom,
            units_per_hour=Decimal("180"), rate_uom=self.kg,
        )
        RoutingOperation.objects.create(
            routing=routing, sequence=20, name="Coat", is_outside=True,
            outside_lead_days=5, outside_cost_per_unit=Decimal("2"),
            rate_uom=self.kg,
        )
        self.bom.routing = routing
        self.bom.save()
        self.run = self.order("1000")
        self.run.release(TODAY)
        self.step = self.run.operations.get(is_outside=True)

    def purchase(self, price="2.10", quantity="1000", **kwargs):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=TODAY,
        )
        kwargs.setdefault("item", self.coating)
        kwargs.setdefault("uom", self.kg)
        kwargs.setdefault("work_order_operation", self.step)
        line = PurchaseOrderLine.objects.create(
            order=order, quantity=Decimal(quantity),
            unit_price=Decimal(price), **kwargs,
        )
        return order, line

    def receive(self, order, quantity):
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=TODAY,
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order.lines.get(),
            warehouse=self.plant, quantity_received=Decimal(quantity),
        )
        receipt.post()
        return receipt

    def bill(self, line, quantity, price):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=TODAY, payable_account=self.payable,
            purchase_order=line.order,
        )
        BillLine.objects.create(
            bill=bill, item=self.coating, order_line=line,
            quantity=Decimal(quantity), unit_price=Decimal(price),
        )
        bill.post()
        return bill

    def balance(self, account):
        total = Decimal("0")
        for row in JournalLine.objects.filter(account=account, entry__posted=True):
            total += row.debit - row.credit
        return total


class ALinePaysForExactlyOneThingTests(OutsidePurchaseTestCase):
    def test_a_step_of_our_own_cannot_be_paid_for(self):
        with self.assertRaisesMessage(ValidationError, "our own machines"):
            self.purchase(
                work_order_operation=self.run.operations.get(is_outside=False)
            )

    def test_a_stocked_item_is_not_a_vendors_service(self):
        with self.assertRaisesMessage(ValidationError, "not a stocked item"):
            self.purchase(item=self.virgin)

    def test_whole_item_job_work_is_not_paid_twice(self):
        with self.assertRaisesMessage(ValidationError, "twice"):
            self.purchase(bom=self.bom)

    def test_the_line_counts_what_the_run_counts(self):
        pcs = UnitOfMeasure.objects.create(
            code="pcs", name="Pieces", category=UnitOfMeasureCategory.COUNT
        )
        service = Item.objects.create(
            sku="SVC-PCS", name="Per-piece service", uom=pcs,
            track_inventory=False,
        )
        with self.assertRaisesMessage(ValidationError, "count the same thing"):
            self.purchase(item=service, uom=pcs)

    def test_a_drop_ship_never_passes_through_the_plant(self):
        from apps.sales.models import SalesOrder

        customer = Party.objects.create(code="C-1", name="Cement Co")
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        sale = SalesOrder.objects.create(
            customer=customer, order_date=TODAY, currency=self.usd,
        )
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=TODAY, drop_ship_for=sale,
        )
        with self.assertRaisesMessage(ValidationError, "drop-ship"):
            PurchaseOrderLine.objects.create(
                order=order, item=self.coating, uom=self.kg,
                quantity=Decimal("10"), unit_price=Decimal("2"),
                work_order_operation=self.step,
            )


class TheReceiptGoesIntoTheRunTests(OutsidePurchaseTestCase):
    def test_work_in_progress_takes_the_accrual(self):
        order, _line = self.purchase()
        receipt = self.receive(order, "1000")
        self.assertEqual(self.balance(self.wip), Decimal("2100.00"))
        self.assertEqual(self.balance(self.grni), Decimal("-2100.00"))
        self.assertEqual(self.run.outside_cost(), Decimal("2100.00"))
        movement = receipt.lines.get().outside_movement
        self.assertIsNotNone(movement)
        self.assertEqual(movement.quantity, Decimal("1000"))

    def test_and_nothing_lands_on_a_shelf(self):
        before = StockMovement.objects.count()
        order, _line = self.purchase()
        self.receive(order, "1000")
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(self.balance(self.inventory), Decimal("0"))

    def test_an_ordinary_service_line_still_posts_nothing(self):
        """The control. Only a line that names a step goes through the run."""
        order, _line = self.purchase(work_order_operation=None)
        self.receive(order, "1000")
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertEqual(self.balance(self.grni), Decimal("0"))


class SendingItBackReversesItTests(OutsidePurchaseTestCase):
    def test_a_partial_return_takes_its_share_back_out(self):
        order, _line = self.purchase()
        receipt = self.receive(order, "1000")
        receipt.create_return({receipt.lines.get(): "400"}, debit_bills=False)
        # 600 kg at 2.10 stays in the run.
        self.assertEqual(self.balance(self.wip), Decimal("1260.00"))
        self.assertEqual(self.balance(self.grni), Decimal("-1260.00"))
        self.assertEqual(self.step.quantity_back(), Decimal("600"))


class TheBillClearsTheAccrualTests(OutsidePurchaseTestCase):
    def test_a_bill_at_the_agreed_price_clears_it_to_nothing(self):
        order, line = self.purchase()
        self.receive(order, "1000")
        self.bill(line, "1000", "2.10")
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.services), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-2100.00"))

    def test_a_dearer_bill_puts_the_difference_in_price_variance(self):
        """Not on the run: the run took the agreed price at receipt."""
        company = Company.get()
        company.purchase_price_tolerance_percent = Decimal("10")
        company.save()
        order, line = self.purchase()
        self.receive(order, "1000")
        self.bill(line, "1000", "2.25")
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.ppv), Decimal("150.00"))
        self.assertEqual(self.run.outside_cost(), Decimal("2100.00"))

    def test_an_ordinary_service_bill_still_expenses(self):
        order, line = self.purchase(work_order_operation=None)
        self.receive(order, "1000")
        self.bill(line, "1000", "2.10")
        self.assertEqual(self.balance(self.services), Decimal("2100.00"))
        self.assertEqual(self.balance(self.grni), Decimal("0"))

    def test_returned_after_billing_the_debit_note_clears_it_again(self):
        """
        The whole round trip, both directions: back, billed, sent back,
        debited. Nothing is left in the accrual or in the run.
        """
        order, line = self.purchase()
        receipt = self.receive(order, "1000")
        self.bill(line, "1000", "2.10")
        receipt.create_return()
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("0"))
