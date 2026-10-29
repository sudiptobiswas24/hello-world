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


class CommissionOnCollectedCashLeavesTheTaxOutTests(SalesTestCase):
    """O294 (probe S8)."""

    def collected_at_five_percent(self, paid):
        """Invoice 10 x 100 + 20% VAT = 1,200, and `paid` of it. 5% of the net sale 1,000 is 50.00 on either
        basis; counting the payment, tax included, the collected basis paid 60.00."""
        from apps.accounting.models import Tax, TaxGroup
        from .models import CommissionBasis, CommissionPlan, SalesRep, commission_report

        group = TaxGroup.objects.create(code="VAT", name="VAT")
        vat_account = Account.objects.create(code="2100", name="VAT Payable", account_type=AccountType.LIABILITY)
        vat = Tax.objects.create(code="VAT20", name="VAT 20%", rate=Decimal("20"), group=group,
                                 collected_account=vat_account, paid_account=vat_account)
        plan = CommissionPlan.objects.create(code="P", name="5% collected", percent=Decimal("5"),
                                             basis=CommissionBasis.PAID)
        SalesRep.objects.create(party=self.rep, plan=plan)
        order = self.make_order("10", "100", rep=self.rep)
        for line in order.lines.all():
            line.taxes.set([vat])
        invoice = self.bill(order)
        self.assertEqual(invoice.total(), Decimal("1200.00"))
        self.allocate(self.receipt(paid), invoice, paid)
        return commission_report()[0]

    def test_paid_in_full_it_pays_on_the_net_sale(self):
        row = self.collected_at_five_percent("1200")
        self.assertEqual((row["basis_amount"], row["commission"]), (Decimal("1000.00"), Decimal("50.00")))

    def test_paid_in_part_it_pays_on_the_net_share_of_the_money(self):
        # 600 of 1,200 paid: 500 of the net sale, 25.00.
        row = self.collected_at_five_percent("600")
        self.assertEqual((row["basis_amount"], row["commission"]), (Decimal("500.00"), Decimal("25.00")))


class CommissionLeavesTcsOutTests(SalesTestCase):
    """
    O350 (decided): TCS is not revenue. 10 x 100 + 20% VAT = 1,200 and 12.00 of TCS: 1,212. The collected basis
    counts money up to the 1,200 the sale and its tax come to, at the net share 1,000 / 1,200
    (calc_fix_books3/o350.py). TCS in the divisor, 600 paid counted 495.05 and a buyer who withheld the TCS
    and paid 1,200 counted 990.10. The TCS is planted on the invoice: the plan reads only its amount.
    """

    def invoice_with_tcs(self):
        from apps.accounting.models import Tax, TaxGroup
        from .models import CommissionBasis, CommissionPlan, Invoice, SalesRep

        group = TaxGroup.objects.create(code="VAT", name="VAT")
        vat_account = Account.objects.create(code="2100", name="VAT Payable", account_type=AccountType.LIABILITY)
        vat = Tax.objects.create(code="VAT20", name="VAT 20%", rate=Decimal("20"), group=group,
                                 collected_account=vat_account, paid_account=vat_account)
        plan = CommissionPlan.objects.create(code="P", name="5% collected", percent=Decimal("5"),
                                             basis=CommissionBasis.PAID)
        SalesRep.objects.create(party=self.rep, plan=plan)
        order = self.make_order("10", "100", rep=self.rep)
        for line in order.lines.all():
            line.taxes.set([vat])
        invoice = self.bill(order)
        Invoice.objects.filter(pk=invoice.pk).update(tcs_amount=Decimal("12.00"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.total(), Decimal("1212.00"))
        return invoice

    def basis(self, **period):
        from .models import commission_report

        row = commission_report(**period)[0]
        return row["basis_amount"], row["commission"]

    def test_paid_in_part_the_tcs_is_not_in_the_divisor(self):
        invoice = self.invoice_with_tcs()
        self.allocate(self.receipt("600"), invoice, "600")
        self.assertEqual(self.basis(), (Decimal("500.00"), Decimal("25.00")))

    def test_the_sale_and_its_tax_paid_without_the_tcs_pays_the_whole_net_sale(self):
        invoice = self.invoice_with_tcs()
        self.allocate(self.receipt("1200"), invoice, "1200")
        self.assertEqual(self.basis(), (Decimal("1000.00"), Decimal("50.00")))

    def test_paid_in_full_across_two_months(self):
        # 600 in March, 612 in April: 500.00 each; the 12.00 past the sale's value counts in no month.
        invoice = self.invoice_with_tcs()
        self.allocate(self.receipt("600"), invoice, "600")
        self.allocate(self.receipt("612", on=datetime.date(2026, 4, 10)), invoice, "612")
        march = self.basis(date_from=datetime.date(2026, 3, 1), date_to=datetime.date(2026, 3, 31))
        april = self.basis(date_from=datetime.date(2026, 4, 1), date_to=datetime.date(2026, 4, 30))
        self.assertEqual((march, april, self.basis()),
                         ((Decimal("500.00"), Decimal("25.00")), (Decimal("500.00"), Decimal("25.00")),
                          (Decimal("1000.00"), Decimal("50.00"))))


class ADunningReminderOnlyGoesUpTests(SalesTestCase):
    """O295 (probe S7)."""

    def test_after_the_60_day_notice_neither_the_30_nor_the_7_day_one_follows(self):
        """Levels 7, 30, 60. Run first at 70 days overdue: the 60-day notice. Run again next day: nothing
        (or a higher level), never the 30-day and then the 7-day reminder after the final notice."""
        from django.core import mail
        from .models import DunningLevel, DunningNotice, run_dunning

        for name, days in (("First", 7), ("Second", 30), ("Final", 60)):
            DunningLevel.objects.create(name=name, days_overdue=days)
        order = self.make_order(quantity="10", price="100")
        self.ship(order, 10)
        invoice = self.bill(order, on=datetime.date(2026, 3, 1))   # net 30: due 31 March
        first_day = invoice.due_date + datetime.timedelta(days=70)
        run_dunning(as_of=first_day)
        run_dunning(as_of=first_day + datetime.timedelta(days=1))
        run_dunning(as_of=first_day + datetime.timedelta(days=2))
        sent = list(DunningNotice.objects.filter(invoice=invoice).order_by("id").values_list("level__days_overdue", flat=True))
        self.assertEqual(sent, [60], f"levels sent in order: {sent}")

    def test_each_level_goes_out_in_turn_as_the_invoice_ages(self):
        from .models import DunningLevel, DunningNotice, run_dunning

        for name, days in (("First", 7), ("Second", 30), ("Final", 60)):
            DunningLevel.objects.create(name=name, days_overdue=days)
        order = self.make_order(quantity="10", price="100")
        self.ship(order, 10)
        invoice = self.bill(order, on=datetime.date(2026, 3, 1))
        for days in (10, 11, 35, 36, 65, 66):
            run_dunning(as_of=invoice.due_date + datetime.timedelta(days=days), send=False)
        sent = list(DunningNotice.objects.filter(invoice=invoice).order_by("id").values_list("level__days_overdue", flat=True))
        self.assertEqual(sent, [7, 30, 60])


class ADunningRunReadsTheInvoiceUnderItsHoldTests(SalesTestCase):
    """Review of O296: what is due is read again once the invoice is held, not from the run's first read."""

    def test_an_invoice_paid_as_the_run_reaches_it_is_not_chased(self):
        from unittest import mock

        from . import models
        from .models import DunningLevel, DunningNotice, run_dunning

        DunningLevel.objects.create(name="First", days_overdue=7)
        order = self.make_order(quantity="10", price="100")
        self.ship(order, 10)
        invoice = self.bill(order, on=datetime.date(2026, 3, 1))
        real = models.lock_rows
        paid = []

        def paid_first(*rows, **kwargs):
            # The customer's 1,000 is allocated after the run read the invoice, before it is held.
            if not paid and any(isinstance(row, models.Invoice) for row in rows):
                paid.append(True)
                # Another person's transaction, which on SQLite cannot run beside this one: the lock-order
                # sentinel counts it apart, or its receipt's number reads as this run's (O257).
                from django.db import connection

                def apart(step):
                    held, connection._lock_order_state = getattr(connection, "_lock_order_state", None), None
                    try:
                        return step()
                    finally:
                        connection._lock_order_state = held

                money = apart(lambda: self.receipt("1000"))
                apart(lambda: self.allocate(money, invoice, "1000"))
            return real(*rows, **kwargs)

        with mock.patch.object(models, "lock_rows", paid_first):
            run_dunning(as_of=invoice.due_date + datetime.timedelta(days=10), send=False)
        self.assertEqual(DunningNotice.objects.filter(invoice=invoice).count(), 0)
