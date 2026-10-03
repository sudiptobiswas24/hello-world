"""
Second audit pass. Two of these are the same shape as earlier findings:
a feature fully built and tested in isolation that no code path ever
invoked, so it sat there looking handled.
"""

import datetime
from decimal import Decimal

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounting.models import (
    Account,
    AccountType,
    JournalLine,
    Payment,
    PaymentDirection,
)
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
    DunningLevel,
    DunningNotice,
    Invoice,
    InvoiceLine,
    InvoicePayment,
    InvoicePolicy,
    Quotation,
    QuotationLine,
    SalesOrder,
    SalesOrderLine,
    SalesRep,
    commission_report,
    run_dunning,
)


from .tests_base import SalesTestCase


class SettlementDiscountTests(SalesTestCase):
    """The terms carried a 2/10 discount that nothing ever applied."""

    def test_the_invoice_knows_its_discount_and_deadline(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.assertEqual(invoice.discount_due_date(), datetime.date(2026, 3, 11))
        self.assertEqual(invoice.settlement_discount(), Decimal("20.00"))

    def test_applying_it_clears_the_remaining_balance(self):
        invoice = self.bill(self.make_order("10", "100"))
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 3, 8), amount=Decimal("980"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("980"))
        self.assertEqual(invoice.amount_due(), Decimal("20.00"))  # the discount, still hanging

        invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 8))

        self.assertEqual(invoice.amount_due(), Decimal("0.00"))
        self.assertEqual(invoice.settlement_status(), "paid")

    def test_it_posts_the_write_off_to_the_ledger(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 8))

        entry = invoice.settlement_discount_entry
        self.assertEqual(entry.lines.get(account=self.discount_account).debit, Decimal("20.00"))
        self.assertEqual(entry.lines.get(account=self.ar).credit, Decimal("20.00"))
        self.assertEqual(entry.total_debit(), entry.total_credit())

    def test_it_expires_after_the_discount_window(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.assertFalse(invoice.discount_is_available(as_of=datetime.date(2026, 3, 20)))
        with self.assertRaises(ValidationError):
            invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 20))

    def test_it_can_be_granted_late_on_purpose(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 20), force=True)
        self.assertEqual(invoice.settlement_discount_amount, Decimal("20.00"))

    def test_it_cannot_be_taken_twice(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 8))
        with self.assertRaises(ValidationError):
            invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 9))

    def test_terms_with_no_discount_offer_none(self):
        plain = PaymentTerms.objects.create(code="NET30", name="Net 30", net_days=30)
        self.customer.payment_terms = plain
        self.customer.save()
        invoice = self.bill(self.make_order("10", "100"))
        self.assertIsNone(invoice.discount_due_date())
        with self.assertRaises(ValidationError):
            invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 2))

    def test_a_discounted_invoice_is_not_chased(self):
        invoice = self.bill(self.make_order("10", "100"))
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 3, 8), amount=Decimal("980"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("980"))
        invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 8))

        DunningLevel.objects.create(name="Reminder", days_overdue=7)
        self.assertEqual(run_dunning(as_of=datetime.date(2026, 6, 1)), [])


class InvoiceOnDeliveryTests(SalesTestCase):
    """Billing the ordered quantity charged for goods still in the warehouse."""

    def test_a_delivered_policy_bills_only_what_shipped(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.ship(order, "4")

        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        self.assertEqual(invoice.lines.get().quantity, Decimal("4"))
        self.assertEqual(invoice.total(), Decimal("400.00"))

    def test_the_rest_becomes_billable_once_it_ships(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.ship(order, "4")
        self.bill(order, on=datetime.date(2026, 3, 5))

        self.ship(order, "6")
        remainder = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 9))
        self.assertEqual(remainder.lines.get().quantity, Decimal("6"))

    def test_nothing_shipped_means_nothing_to_bill(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        with self.assertRaises(ValidationError) as caught:
            order.create_invoice(self.ar)
        self.assertIn("bills on delivery", str(caught.exception))

    def test_the_ordered_policy_still_bills_everything(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.ORDERED)
        self.ship(order, "4")
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        self.assertEqual(invoice.lines.get().quantity, Decimal("10"))

    def test_invoiceable_never_exceeds_uninvoiced(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.ship(order, "10")
        self.bill(order, on=datetime.date(2026, 3, 5))
        self.assertEqual(order.lines.get().quantity_invoiceable(), Decimal("0"))


class CommissionRefundTests(SalesTestCase):
    """Commission on collected cash survived the cash being handed back."""

    def setUp(self):
        super().setUp()
        plan = CommissionPlan.objects.create(
            code="P", name="5% collected", percent=Decimal("5"), basis=CommissionBasis.PAID
        )
        SalesRep.objects.create(party=self.rep, plan=plan)

    def test_a_refund_removes_the_commission(self):
        invoice = self.bill(self.make_order("10", "100", rep=self.rep))
        receipt = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 3, 10), amount=Decimal("1000"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        receipt.post()
        InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=Decimal("1000"))
        self.assertEqual(commission_report()[0]["commission"], Decimal("50.00"))

        credit_note = invoice.create_credit_note()
        refund = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 3, 20), amount=Decimal("1000"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        refund.post()
        InvoicePayment.objects.create(invoice=credit_note, payment=refund, amount=Decimal("1000"))

        self.assertEqual(commission_report(), [])

    def test_a_partial_refund_reduces_it_proportionally(self):
        invoice = self.bill(self.make_order("10", "100", rep=self.rep))
        receipt = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 3, 10), amount=Decimal("1000"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        receipt.post()
        InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=Decimal("1000"))

        credit_note = invoice.create_credit_note(
            quantities={invoice.lines.get(): Decimal("4")}
        )
        refund = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 3, 20), amount=Decimal("400"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        refund.post()
        InvoicePayment.objects.create(invoice=credit_note, payment=refund, amount=Decimal("400"))

        self.assertEqual(commission_report()[0]["basis_amount"], Decimal("600.00"))
        self.assertEqual(commission_report()[0]["commission"], Decimal("30.00"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class UndeliverableDunningTests(SalesTestCase):
    """A notice recorded but never sent silently retired that reminder level."""

    def setUp(self):
        super().setUp()
        self.level = DunningLevel.objects.create(name="Reminder", days_overdue=7)
        self.silent = Party.objects.create(
            code="C-9", name="No Email", default_currency=self.usd, payment_terms=self.terms
        )
        PartyRoleAssignment.objects.create(party=self.silent, role=PartyRole.CUSTOMER)

    def overdue_invoice(self, customer):
        invoice = Invoice.objects.create(
            customer=customer, invoice_date=datetime.date(2026, 1, 1),
            receivable_account=self.ar, currency=self.usd,
        )
        InvoiceLine.objects.create(
            invoice=invoice, item=self.item, quantity=Decimal("1"),
            unit_price=Decimal("50"), revenue_account=self.revenue,
        )
        invoice.post()
        return invoice

    def test_no_notice_is_recorded_when_it_cannot_be_sent(self):
        self.overdue_invoice(self.silent)
        notices = run_dunning(as_of=datetime.date(2026, 6, 1))

        self.assertEqual(notices, [])
        self.assertEqual(DunningNotice.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_it_is_chased_once_an_address_exists(self):
        self.overdue_invoice(self.silent)
        run_dunning(as_of=datetime.date(2026, 6, 1))

        self.silent.email = "ap@noemail.example"
        self.silent.save()
        notices = run_dunning(as_of=datetime.date(2026, 6, 2))

        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].sent_to, "ap@noemail.example")

    def test_reachable_customers_are_unaffected(self):
        self.overdue_invoice(self.silent)
        self.overdue_invoice(self.customer)

        notices = run_dunning(as_of=datetime.date(2026, 6, 1))
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].invoice.customer, self.customer)

    def test_a_preview_run_records_without_needing_an_address(self):
        self.overdue_invoice(self.silent)
        notices = run_dunning(as_of=datetime.date(2026, 6, 1), send=False)
        self.assertEqual(len(notices), 1)


class DraftQuotationRevisionTests(SalesTestCase):
    def test_a_draft_is_edited_rather_than_revised(self):
        quotation = Quotation.objects.create(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        QuotationLine.objects.create(
            quotation=quotation, item=self.item, uom=self.uom,
            quantity=Decimal("1"), unit_price=Decimal("10"), revenue_account=self.revenue,
        )
        with self.assertRaises(ValidationError) as caught:
            quotation.create_revision()
        self.assertIn("edit it directly", str(caught.exception))

        quotation.refresh_from_db()
        self.assertEqual(quotation.number, "")  # no sequence number burned
        self.assertEqual(quotation.status, "draft")
