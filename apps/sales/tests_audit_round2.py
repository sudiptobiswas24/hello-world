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

    def test_a_paid_invoice_is_not_discounted_into_credit(self):
        # The bill refused a discount beyond what was owed; the invoice never asked, and one paid
        # in full went to -20.00, a credit the customer was never owed.
        invoice = self.bill(self.make_order("10", "100"))
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 3, 8), amount=Decimal("1000"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("1000"))
        with self.assertRaisesMessage(ValidationError, "would take it below zero"):
            invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 8))
        self.assertEqual(invoice.amount_due(), Decimal("0.00"))

    def test_forcing_waives_the_deadline_not_the_once(self):
        # The once was asked only where force skips, so a forced second call wrote it off again.
        invoice = self.bill(self.make_order("10", "100"))
        invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 8))
        with self.assertRaisesMessage(ValidationError, "taken already"):
            invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 9), force=True)
        self.assertEqual(invoice.amount_due(), Decimal("980.00"))

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


class SettlementDiscountOnTheReceiptTests(SalesTestCase):
    """
    O109: the discount for paying early is taken on the receipt that earned
    it, through the API, as a financial discount with GST unchanged. 1,000 on
    2/10 net 30 invoiced 1 March: 980 received on 8 March, 20 discount.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ar-manager")
        user.groups.add(Group.objects.get(name="AR Manager"))
        self.client = APIClient()
        self.client.force_authenticate(user)
        self.invoice = self.bill(self.make_order("10", "100"))

    def take(self, payment):
        return self.client.post(f"/api/sales/invoices/{self.invoice.pk}/take_settlement_discount/",
                                {"payment": payment.pk}, format="json")

    def test_on_a_receipt_within_the_days_it_clears_the_invoice(self):
        receipt = self.receipt("980", on=datetime.date(2026, 3, 8))
        self.allocate(receipt, self.invoice, "980")
        answer = self.take(receipt)
        self.assertEqual(answer.status_code, 200, answer.content)
        self.invoice.refresh_from_db()
        entry = self.invoice.settlement_discount_entry
        self.assertEqual((self.invoice.amount_due(), self.balance(self.ar), entry.date),
                         (Decimal("0.00"), Decimal("0.00"), datetime.date(2026, 3, 8)))
        # A financial discount: the discount account and the receivable, no tax line.
        self.assertEqual(sorted((line.account_id, line.debit, line.credit) for line in entry.lines.all()),
                         sorted([(self.discount_account.pk, Decimal("20.00"), Decimal("0")),
                                 (self.ar.pk, Decimal("0"), Decimal("20.00"))]))

    def test_a_receipt_after_the_days_earns_none(self):
        receipt = self.receipt("980", on=datetime.date(2026, 3, 12))
        self.allocate(receipt, self.invoice, "980")
        answer = self.take(receipt)
        self.assertEqual(answer.status_code, 400)
        self.assertIn("after the discount days", str(answer.content))

    def test_a_receipt_not_applied_to_it_earns_none(self):
        receipt = self.receipt("980", on=datetime.date(2026, 3, 8))
        answer = self.take(receipt)
        self.assertEqual(answer.status_code, 400)
        self.assertIn("is not applied to", str(answer.content))

    def test_terms_that_reduce_the_gst_value_give_it_as_a_credit_note(self):
        self.terms.discount_reduces_gst = True
        self.terms.save()
        receipt = self.receipt("980", on=datetime.date(2026, 3, 8))
        self.allocate(receipt, self.invoice, "980")
        answer = self.take(receipt)
        self.assertEqual(answer.status_code, 400)
        self.assertIn("issue a credit note", str(answer.content))


class TheDunningMailAsksForWhatIsLateTests(SalesTestCase):
    """O81 (DunningProbe): the mail says what the notice records as overdue, and since when."""

    def test_the_reminder_asks_for_what_is_late(self):
        from django.core import mail

        from apps.core.models import PaymentTermsLine
        from .models import DunningLevel

        split = PaymentTerms.objects.create(code="5050", name="50/50", net_days=30)
        PaymentTermsLine.objects.create(terms=split, sequence=1, percent=Decimal("50"), days=0)
        PaymentTermsLine.objects.create(terms=split, sequence=2, percent=Decimal("50"), days=30)
        self.customer.payment_terms = split
        self.customer.save()
        DunningLevel.objects.create(name="First", days_overdue=7)
        self.bill(self.make_order("10", "100"), on=datetime.date(2026, 3, 1))
        notices = run_dunning(as_of=datetime.date(2026, 3, 15), send=True)
        self.assertEqual([notice.amount_due for notice in notices], [Decimal("500.00")])
        body = mail.outbox[0].body
        self.assertTrue("500.00" in body and "01 Mar 2026" in body,
                        f"notice records 500.00 overdue since 01 Mar 2026; the mail says: {body!r}")
