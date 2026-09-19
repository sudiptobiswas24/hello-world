"""Backorders, quotation revisions, rep commissions, recurring invoicing."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import Account, AccountType, Payment, PaymentDirection, Tax
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    PaymentTerms,
    UnitOfMeasure,
)
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse

from .models import (
    CommissionBasis,
    CommissionPlan,
    Delivery,
    DeliveryLine,
    Invoice,
    InvoicePayment,
    Quotation,
    QuotationLine,
    QuotationStatus,
    RecurrenceInterval,
    RecurringInvoice,
    RecurringInvoiceLine,
    SalesOrder,
    SalesOrderLine,
    SalesRep,
    add_interval,
    commission_report,
    generate_due_invoices,
)


class LifecycleTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(
            sku="WDG-1", name="Widget", uom=self.uom, sale_price=Decimal("10")
        )
        self.warehouse = Warehouse.objects.create(code="WH1", name="Main")

        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET)
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        self.inventory = Account.objects.create(
            code="1200", name="Inventory", account_type=AccountType.ASSET
        )
        self.cogs = Account.objects.create(
            code="5000", name="COGS", account_type=AccountType.EXPENSE
        )
        self.grni = Account.objects.create(
            code="2150", name="GRNI", account_type=AccountType.LIABILITY
        )
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory, default_cogs_account=self.cogs,
            grni_account=self.grni,
        )
        self.net30 = PaymentTerms.objects.create(code="NET30", name="Net 30", net_days=30)
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd, payment_terms=self.net30
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

        self.rep_party = Party.objects.create(code="E-1", name="Dana Rep")
        PartyRoleAssignment.objects.create(party=self.rep_party, role=PartyRole.EMPLOYEE)

    def stock_up(self, quantity="100"):
        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
            quantity=Decimal(quantity), unit_cost=Decimal("4"), occurred_at=timezone.now(),
        )

    def make_order(self, quantity="10", price="10", rep=None):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1),
            currency=self.usd, sales_rep=rep,
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
            unit_price=Decimal(price), revenue_account=self.revenue,
        )
        order.confirm()
        return order

    def ship(self, order, quantity):
        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 3, 4)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=order.lines.get(),
            warehouse=self.warehouse, quantity_shipped=Decimal(quantity),
        )
        delivery.post()
        return delivery


class BackorderTests(LifecycleTestCase):
    def test_a_short_shipment_reports_its_shortfall(self):
        self.stock_up()
        order = self.make_order("10")
        delivery = self.ship(order, "4")

        shortfall = delivery.shortfall()
        self.assertEqual(list(shortfall.values()), [Decimal("6")])

    def test_a_backorder_carries_the_remainder(self):
        self.stock_up()
        order = self.make_order("10")
        delivery = self.ship(order, "4")

        backorder = delivery.create_backorder(delivery_date=datetime.date(2026, 3, 10))

        self.assertEqual(backorder.backorder_of, delivery)
        self.assertFalse(backorder.posted)
        self.assertEqual(backorder.lines.get().quantity_shipped, Decimal("6"))
        self.assertEqual(backorder.sales_order, order)

    def test_shipping_the_backorder_completes_the_order(self):
        self.stock_up()
        order = self.make_order("10")
        delivery = self.ship(order, "4")
        backorder = delivery.create_backorder()
        backorder.post()

        self.assertEqual(order.lines.get().quantity_shipped(), Decimal("10"))
        self.assertEqual(order.delivery_status(), "full")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("90"))

    def test_a_complete_shipment_has_no_backorder(self):
        self.stock_up()
        order = self.make_order("10")
        delivery = self.ship(order, "10")
        with self.assertRaises(ValidationError):
            delivery.create_backorder()

    def test_a_delivery_cannot_be_backordered_twice(self):
        self.stock_up()
        order = self.make_order("10")
        delivery = self.ship(order, "4")
        delivery.create_backorder()
        with self.assertRaises(ValidationError):
            delivery.create_backorder()

    def test_an_unposted_delivery_has_no_backorder(self):
        self.stock_up()
        order = self.make_order("10")
        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 3, 4)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=order.lines.get(),
            warehouse=self.warehouse, quantity_shipped=Decimal("4"),
        )
        with self.assertRaises(ValidationError):
            delivery.create_backorder()

    def test_a_return_does_not_leave_a_backorder(self):
        self.stock_up()
        order = self.make_order("10")
        delivery = self.ship(order, "10")
        customer_return = delivery.create_return(credit_invoices=False)
        with self.assertRaises(ValidationError):
            customer_return.create_backorder()


class QuotationRevisionTests(LifecycleTestCase):
    def make_quotation(self, quantity="5", price="10"):
        quotation = Quotation.objects.create(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        QuotationLine.objects.create(
            quotation=quotation, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            revenue_account=self.revenue,
        )
        return quotation

    def test_a_revision_supersedes_the_original(self):
        quotation = self.make_quotation()
        quotation.mark_sent()

        revision = quotation.create_revision(quotation_date=datetime.date(2026, 3, 8))

        quotation.refresh_from_db()
        self.assertEqual(quotation.status, QuotationStatus.SUPERSEDED)
        self.assertEqual(revision.revision_of, quotation)
        self.assertEqual(revision.revision, 2)
        self.assertEqual(revision.status, QuotationStatus.DRAFT)

    def test_a_revision_keeps_the_number_and_adds_its_revision(self):
        quotation = self.make_quotation()
        quotation.mark_sent()
        self.assertEqual(quotation.number, "QT-2026-00001")

        revision = quotation.create_revision()
        self.assertEqual(revision.number, "")
        revision.mark_sent()
        self.assertEqual(revision.number, "QT-2026-00001-R2")

    def test_revisions_stack(self):
        quotation = self.make_quotation()
        quotation.mark_sent()
        second = quotation.create_revision()
        second.mark_sent()
        third = second.create_revision()
        third.mark_sent()

        self.assertEqual(third.revision, 3)
        self.assertEqual(third.number, "QT-2026-00001-R3")

    def test_a_revision_copies_the_lines_and_is_editable(self):
        quotation = self.make_quotation("5", "10")
        quotation.mark_sent()
        revision = quotation.create_revision()

        line = revision.lines.get()
        self.assertEqual(line.quantity, Decimal("5"))
        line.unit_price = Decimal("8")
        line.save()
        self.assertEqual(revision.total(), Decimal("40.00"))

    def test_the_superseded_quote_keeps_its_original_figures(self):
        quotation = self.make_quotation("5", "10")
        quotation.mark_sent()
        revision = quotation.create_revision()
        revision.lines.update(unit_price=Decimal("8"))

        quotation.refresh_from_db()
        self.assertEqual(quotation.total(), Decimal("50.00"))
        self.assertEqual(revision.total(), Decimal("40.00"))

    def test_a_superseded_quote_cannot_be_accepted(self):
        quotation = self.make_quotation()
        quotation.mark_sent()
        quotation.create_revision()
        with self.assertRaises(ValidationError):
            quotation.accept()

    def test_a_superseded_quote_cannot_be_revised_again(self):
        quotation = self.make_quotation()
        quotation.mark_sent()
        quotation.create_revision()
        with self.assertRaises(ValidationError):
            quotation.create_revision()

    def test_an_accepted_quote_cannot_be_revised(self):
        quotation = self.make_quotation()
        quotation.mark_sent()
        quotation.accept(order_date=datetime.date(2026, 3, 5))
        with self.assertRaises(ValidationError):
            quotation.create_revision()

    def test_accepting_a_revision_produces_the_revised_order(self):
        quotation = self.make_quotation("5", "10")
        quotation.mark_sent()
        revision = quotation.create_revision()
        revision.lines.update(unit_price=Decimal("8"))
        revision.mark_sent()

        order = revision.accept(order_date=datetime.date(2026, 3, 12))
        self.assertEqual(order.total(), Decimal("40.00"))


class CommissionTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.plan = CommissionPlan.objects.create(
            code="STD", name="Standard 5%", percent=Decimal("5"),
            basis=CommissionBasis.INVOICED,
        )
        self.rep = SalesRep.objects.create(party=self.rep_party, plan=self.plan)

    def bill(self, order):
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        invoice.post()
        return invoice

    def test_a_rep_must_be_an_employee(self):
        outsider = Party.objects.create(code="X-1", name="Not staff")
        rep = SalesRep(party=outsider, plan=self.plan)
        with self.assertRaises(ValidationError):
            rep.full_clean()

    def test_commission_on_what_was_invoiced(self):
        order = self.make_order("10", "100", rep=self.rep_party)
        self.bill(order)

        rows = commission_report()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["basis_amount"], Decimal("1000.00"))
        self.assertEqual(rows[0]["commission"], Decimal("50.00"))

    def test_the_rep_carries_from_order_to_invoice(self):
        order = self.make_order("10", "100", rep=self.rep_party)
        invoice = self.bill(order)
        self.assertEqual(invoice.sales_rep, self.rep_party)

    def test_credit_notes_reduce_commission(self):
        order = self.make_order("10", "100", rep=self.rep_party)
        invoice = self.bill(order)
        invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("4")})

        rows = commission_report()
        self.assertEqual(rows[0]["basis_amount"], Decimal("600.00"))
        self.assertEqual(rows[0]["commission"], Decimal("30.00"))

    def test_an_unattributed_sale_earns_nobody_commission(self):
        order = self.make_order("10", "100")  # no rep
        self.bill(order)
        self.assertEqual(commission_report(), [])

    def test_commission_on_cash_collected(self):
        self.plan.basis = CommissionBasis.PAID
        self.plan.save()

        order = self.make_order("10", "100", rep=self.rep_party)
        invoice = self.bill(order)
        self.assertEqual(commission_report(), [])  # nothing collected yet

        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 3, 20), amount=Decimal("400"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("400"))

        rows = commission_report()
        self.assertEqual(rows[0]["basis_amount"], Decimal("400.00"))
        self.assertEqual(rows[0]["commission"], Decimal("20.00"))

    def test_the_period_is_respected(self):
        order = self.make_order("10", "100", rep=self.rep_party)
        self.bill(order)  # invoiced 2026-03-05

        self.assertEqual(
            commission_report(date_from=datetime.date(2026, 4, 1)), []
        )
        self.assertEqual(
            len(commission_report(date_to=datetime.date(2026, 3, 31))), 1
        )

    def test_a_rep_without_a_plan_is_skipped(self):
        self.rep.plan = None
        self.rep.save()
        order = self.make_order("10", "100", rep=self.rep_party)
        self.bill(order)
        self.assertEqual(commission_report(), [])


class RecurringInvoiceTests(LifecycleTestCase):
    def make_schedule(self, interval=RecurrenceInterval.MONTHLY, count=1,
                      start=datetime.date(2026, 1, 15), end=None, auto_post=False):
        schedule = RecurringInvoice.objects.create(
            code="SUB-1", customer=self.customer, receivable_account=self.ar,
            currency=self.usd, interval=interval, interval_count=count,
            start_date=start, end_date=end, auto_post=auto_post,
        )
        RecurringInvoiceLine.objects.create(
            schedule=schedule, item=self.item, description="Monthly retainer",
            quantity=Decimal("1"), unit_price=Decimal("500"), revenue_account=self.revenue,
        )
        return schedule

    def test_the_first_run_is_the_start_date(self):
        schedule = self.make_schedule()
        self.assertEqual(schedule.next_run_date, datetime.date(2026, 1, 15))

    def test_generating_issues_an_invoice_and_advances(self):
        schedule = self.make_schedule()
        invoice = schedule.generate_one()

        self.assertEqual(invoice.invoice_date, datetime.date(2026, 1, 15))
        self.assertEqual(invoice.total(), Decimal("500.00"))
        self.assertFalse(invoice.posted)
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_date, datetime.date(2026, 2, 15))

    def test_auto_post_posts_the_invoice(self):
        schedule = self.make_schedule(auto_post=True)
        invoice = schedule.generate_one()
        self.assertTrue(invoice.posted)
        self.assertTrue(invoice.number.startswith("INV-"))

    def test_a_late_run_catches_up_one_invoice_per_period(self):
        self.make_schedule(start=datetime.date(2026, 1, 15))
        issued = generate_due_invoices(as_of=datetime.date(2026, 4, 20))

        self.assertEqual(len(issued), 4)  # Jan, Feb, Mar, Apr
        self.assertEqual(
            [invoice.invoice_date for invoice in issued],
            [datetime.date(2026, 1, 15), datetime.date(2026, 2, 15),
             datetime.date(2026, 3, 15), datetime.date(2026, 4, 15)],
        )

    def test_nothing_is_issued_before_the_start(self):
        self.make_schedule(start=datetime.date(2026, 6, 1))
        self.assertEqual(generate_due_invoices(as_of=datetime.date(2026, 3, 1)), [])

    def test_the_end_date_stops_the_series(self):
        self.make_schedule(
            start=datetime.date(2026, 1, 15), end=datetime.date(2026, 3, 1)
        )
        issued = generate_due_invoices(as_of=datetime.date(2026, 12, 1))
        self.assertEqual(len(issued), 2)  # Jan and Feb; March is past the end

    def test_an_inactive_schedule_issues_nothing(self):
        schedule = self.make_schedule()
        schedule.is_active = False
        schedule.save()
        self.assertEqual(generate_due_invoices(as_of=datetime.date(2026, 6, 1)), [])

    def test_a_schedule_with_no_lines_issues_nothing(self):
        schedule = self.make_schedule()
        schedule.lines.all().delete()
        self.assertEqual(generate_due_invoices(as_of=datetime.date(2026, 6, 1)), [])
        with self.assertRaises(ValidationError):
            schedule.generate_one()

    def test_quarterly_and_yearly_intervals(self):
        quarterly = self.make_schedule(interval=RecurrenceInterval.QUARTERLY)
        quarterly.generate_one()
        quarterly.refresh_from_db()
        self.assertEqual(quarterly.next_run_date, datetime.date(2026, 4, 15))

    def test_month_end_dates_clamp_rather_than_overflow(self):
        self.assertEqual(
            add_interval(datetime.date(2026, 1, 31), RecurrenceInterval.MONTHLY),
            datetime.date(2026, 2, 28),
        )
        self.assertEqual(
            add_interval(datetime.date(2026, 3, 31), RecurrenceInterval.MONTHLY),
            datetime.date(2026, 4, 30),
        )

    def test_a_clamped_date_returns_to_its_anchor_day(self):
        """Jan 31 -> Feb 28 -> Mar 31, not Feb 28 -> Mar 28."""
        self.assertEqual(
            add_interval(datetime.date(2026, 2, 28), RecurrenceInterval.MONTHLY, anchor_day=31),
            datetime.date(2026, 3, 31),
        )

    def test_a_month_end_schedule_does_not_drift(self):
        self.make_schedule(start=datetime.date(2026, 1, 31))
        issued = generate_due_invoices(as_of=datetime.date(2026, 4, 30))
        self.assertEqual(
            [invoice.invoice_date for invoice in issued],
            [datetime.date(2026, 1, 31), datetime.date(2026, 2, 28),
             datetime.date(2026, 3, 31), datetime.date(2026, 4, 30)],
        )

    def test_every_other_month(self):
        schedule = self.make_schedule(count=2)
        schedule.generate_one()
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_date, datetime.date(2026, 3, 15))

    def test_the_end_date_cannot_precede_the_start(self):
        schedule = RecurringInvoice(
            code="BAD", customer=self.customer, receivable_account=self.ar,
            start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            schedule.full_clean()
