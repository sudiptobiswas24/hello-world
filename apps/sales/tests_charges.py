"""
Non-item charges: freight, handling, surcharges.

The trap this avoids is inventing an Item for shipping. That routes
freight through inventory valuation, and — because a charge has revenue
but no cost of goods — folds recharged shipping into product margin, so
gross margin quietly improves every time the company posts a parcel.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, ChargeType, Tax, TaxGroup

from .models import (
    Delivery,
    DeliveryLine,
    FulfilmentStatus,
    InvoicePolicy,
    Quotation,
    QuotationLine,
    SalesOrderLine,
    revenue_report,
)
from .tests_base import SalesTestCase


class ChargeLineTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.freight_revenue = Account.objects.create(
            code="4100", name="Freight Recharged", account_type=AccountType.INCOME
        )
        self.freight = ChargeType.objects.create(
            code="FRT", name="Shipping", revenue_account=self.freight_revenue
        )


class ChargeBasicsTests(ChargeLineTestCase):
    def test_a_charge_bills_to_its_own_revenue_account(self):
        """Keeping it out of product revenue is the whole point: freight
        with no cost behind it otherwise flatters gross margin."""
        order = self.make_order("10", "100")
        order.add_charge(self.freight, Decimal("50"))
        invoice = self.bill(order)

        self.assertEqual(invoice.total(), Decimal("1050"))
        self.assertEqual(self.balance(self.revenue), Decimal("-1000"))
        self.assertEqual(self.balance(self.freight_revenue), Decimal("-50"))

    def test_a_charge_needs_no_item_or_uom(self):
        order = self.make_order("10", "100")
        line = order.add_charge(self.freight, Decimal("50"))
        self.assertIsNone(line.item_id)
        self.assertIsNone(line.uom_id)
        self.assertTrue(line.is_charge())

    def test_a_line_is_an_item_or_a_charge_never_both(self):
        order = self.make_order("10", "100")
        with self.assertRaises(Exception):
            SalesOrderLine.objects.create(
                order=order, item=self.item, charge=self.freight, uom=self.uom,
                quantity=Decimal("1"), unit_price=Decimal("50"),
                revenue_account=self.freight_revenue,
            )

    def test_a_charge_needs_an_explicit_amount(self):
        """There is no price list for shipping to fall back on."""
        order = self.make_order("10", "100")
        with self.assertRaisesMessage(ValidationError, "explicit amount"):
            SalesOrderLine.objects.create(
                order=order, charge=self.freight, quantity=Decimal("1"), unit_price=None
            )

    def test_it_takes_the_revenue_account_from_the_charge(self):
        order = self.make_order("10", "100")
        line = SalesOrderLine.objects.create(
            order=order, charge=self.freight, quantity=Decimal("1"),
            unit_price=Decimal("50"),
        )
        self.assertEqual(line.revenue_account, self.freight_revenue)

    def test_it_shows_its_own_name_on_the_document(self):
        order = self.make_order("10", "100")
        line = order.add_charge(self.freight, Decimal("50"))
        self.assertEqual(line.label(), "Shipping")
        self.assertIn("Shipping", str(line))


class ChargeTaxTests(ChargeLineTestCase):
    def setUp(self):
        super().setUp()
        group = TaxGroup.objects.create(code="VAT", name="VAT")
        self.vat_account = Account.objects.create(
            code="2100", name="VAT Payable", account_type=AccountType.LIABILITY
        )
        self.vat = Tax.objects.create(
            code="VAT20", name="VAT 20%", rate=Decimal("20"), group=group,
            collected_account=self.vat_account, paid_account=self.vat_account,
        )
        self.freight.taxes.set([self.vat])

    def test_the_charge_brings_its_taxes_with_it(self):
        """Billing freight untaxed where the jurisdiction taxes it is the
        commonest way to get this wrong."""
        order = self.make_order("10", "100")
        line = order.add_charge(self.freight, Decimal("50"))
        self.assertEqual(line.tax_total(), Decimal("10.00"))

    def test_the_tax_reaches_the_invoice_and_the_ledger(self):
        order = self.make_order("10", "100")
        order.add_charge(self.freight, Decimal("50"))
        invoice = self.bill(order)

        self.assertEqual(invoice.total(), Decimal("1060"))
        self.assertEqual(self.balance(self.vat_account), Decimal("-10"))


class ChargeFulfilmentTests(ChargeLineTestCase):
    def test_a_charge_cannot_be_shipped(self):
        order = self.make_order("10", "100")
        charge_line = order.add_charge(self.freight, Decimal("50"))
        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 3, 3)
        )
        with self.assertRaisesMessage(ValidationError, "nothing to ship"):
            DeliveryLine.objects.create(
                delivery=delivery, order_line=charge_line,
                warehouse=self.warehouse, quantity_shipped=Decimal("1"),
            )

    def test_a_charge_does_not_hold_the_order_at_partly_delivered(self):
        """Nothing will ever ship against it, so counting it would pin the
        order at PARTIAL forever."""
        order = self.make_order("10", "100")
        order.add_charge(self.freight, Decimal("50"))
        self.ship(order, "10")
        self.assertEqual(order.delivery_status(), FulfilmentStatus.FULL)

    def test_a_charge_is_billable_without_waiting_for_a_delivery(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        charge_line = order.add_charge(self.freight, Decimal("50"))

        invoice = self.bill(order)

        self.assertEqual(invoice.total(), Decimal("50"))
        self.assertEqual(charge_line.quantity_invoiced(), Decimal("1"))

    def test_a_charge_only_bills_once(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        order.add_charge(self.freight, Decimal("50"))
        self.bill(order)
        self.ship(order, "10")
        second = self.bill(order)
        self.assertEqual(second.total(), Decimal("1000"))


class ChargeCorrectionTests(ChargeLineTestCase):
    def test_a_credit_note_gives_the_charge_back_too(self):
        order = self.make_order("10", "100")
        order.add_charge(self.freight, Decimal("50"))
        invoice = self.bill(order)

        note = invoice.create_credit_note()

        self.assertEqual(note.total(), Decimal("1050"))
        self.assertEqual(self.balance(self.freight_revenue), Decimal("0"))

    def test_charge_revenue_is_reported_under_its_own_name(self):
        order = self.make_order("10", "100")
        order.add_charge(self.freight, Decimal("50"))
        self.bill(order)

        rows = {row["key"]: row["net"] for row in revenue_report(group_by="item")}

        self.assertEqual(rows["WDG-1 - Widget"], Decimal("1000"))
        self.assertEqual(rows["Shipping"], Decimal("50"))


class ChargeOnQuotationTests(ChargeLineTestCase):
    def quote_with_charge(self):
        quotation = Quotation.objects.create(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
            valid_until=datetime.date(2027, 4, 1), currency=self.usd,
        )
        QuotationLine.objects.create(
            quotation=quotation, item=self.item, uom=self.uom, quantity=Decimal("10"),
            unit_price=Decimal("100"), revenue_account=self.revenue,
        )
        QuotationLine.objects.create(
            quotation=quotation, charge=self.freight, description="Shipping",
            quantity=Decimal("1"), unit_price=Decimal("50"),
        )
        return quotation

    def test_a_quote_can_carry_a_charge(self):
        self.assertEqual(self.quote_with_charge().total(), Decimal("1050"))

    def test_accepting_carries_the_charge_to_the_order(self):
        quotation = self.quote_with_charge()
        order = quotation.accept()

        charge_line = order.lines.get(charge=self.freight)
        self.assertEqual(charge_line.unit_price, Decimal("50"))
        self.assertEqual(charge_line.revenue_account, self.freight_revenue)
        self.assertEqual(order.total(), Decimal("1050"))

    def test_a_revision_carries_the_charge(self):
        quotation = self.quote_with_charge()
        quotation.mark_sent()
        revision = quotation.create_revision()
        self.assertEqual(revision.total(), Decimal("1050"))
        self.assertTrue(revision.lines.filter(charge=self.freight).exists())
