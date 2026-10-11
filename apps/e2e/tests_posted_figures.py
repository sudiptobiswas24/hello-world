"""
What a document recorded when it posted, and the reports that read it.

Reports over a year of documents read the posted total and each line's
posted amount before tax instead of building every document to work them
out again. These prove the recorded figures are the ones the document
works out, that a report gives the same answer from either, and that a
payment voided after the fact puts its document back in the reports that
skip what is settled.

Revenue scenario, document rounding, VAT 20% on the widget lines
(worked separately, not from the code):

    Acme, 1 Mar   3 x 33.33 less 10%   net  89.99  tax 18.00
                  1 x 33.33            net  33.33  tax  6.66   (24.66 on 123.32)
                  shipping charge      net  50.00
                  2 x 7.50 pallet hire net  15.00
    credit note   the 1 x 33.33 line   net -33.33  tax -6.66
    Bolt, 2 Apr   2 x 10.00            net  20.00  tax  4.00

    by customer   Acme 154.99 / 18.00 / qty 6     Bolt 20.00 / 4.00 / qty 2
    by item       widget 109.99 / 22.00 / qty 5   shipping 50.00   pallet hire 15.00
"""

import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command

from apps.accounting.models import (
    Account,
    AccountType,
    ChargeType,
    Payment,
    Tax,
    TaxGroup,
)
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.purchasing.models import (
    Bill,
    BillLine,
    BillPayment,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
)
from apps.purchasing.models import not_paid_in_full as bills_not_paid_in_full
from apps.sales.models import Invoice, InvoiceLine, InvoicePayment, revenue_report
from apps.sales.models import not_paid_in_full as invoices_not_paid_in_full
from apps.sales.tests_base import SalesTestCase

MARCH = datetime.date(2026, 3, 1)
APRIL = datetime.date(2026, 4, 2)


class RevenueTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        company = Company.get()
        company.tax_rounding = "document"
        company.save()
        vat_account = Account.objects.create(
            code="2100", name="VAT Payable", account_type=AccountType.LIABILITY)
        self.vat = Tax.objects.create(
            code="VAT20", name="VAT 20%", rate=Decimal("20"),
            group=TaxGroup.objects.create(code="VAT", name="VAT"),
            collected_account=vat_account, paid_account=vat_account,
        )
        self.shipping = ChargeType.objects.create(
            code="FRT", name="Shipping", revenue_account=self.revenue)
        self.bolt = Party.objects.create(code="C-2", name="Bolt", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.bolt, role=PartyRole.CUSTOMER)

    def line(self, invoice, quantity, price, discount="0", taxed=True, **what):
        line = InvoiceLine.objects.create(
            invoice=invoice, quantity=Decimal(quantity), unit_price=Decimal(price),
            discount_percent=Decimal(discount), revenue_account=self.revenue, **what)
        if taxed:
            line.taxes.set([self.vat])
        return line

    def book(self):
        acme = Invoice.objects.create(customer=self.customer, invoice_date=MARCH,
                                      receivable_account=self.ar, currency=self.usd)
        self.line(acme, "3", "33.33", discount="10", item=self.item)
        credited = self.line(acme, "1", "33.33", item=self.item)
        self.line(acme, "1", "50", taxed=False, charge=self.shipping, description="Shipping")
        self.line(acme, "2", "7.50", taxed=False, description="Pallet hire")
        acme.post()
        self.credit_note = acme.create_credit_note(quantities={credited: Decimal("1")})

        bolt = Invoice.objects.create(customer=self.bolt, invoice_date=APRIL,
                                      receivable_account=self.ar, currency=self.usd)
        self.line(bolt, "2", "10", item=self.item)
        bolt.post()
        return acme, bolt

    def report(self, group_by):
        return {row["key"]: (row["net"], row["tax"], row["quantity"])
                for row in revenue_report(group_by=group_by)}


class RecordedAtPostingTests(RevenueTestCase):
    def test_each_line_records_its_amount_before_tax(self):
        acme, bolt = self.book()
        lines = InvoiceLine.objects.filter(invoice__in=[acme, bolt, self.credit_note])
        self.assertEqual(sorted(line.posted_net for line in lines), [
            Decimal("15.00"), Decimal("20.00"), Decimal("33.33"), Decimal("33.33"),
            Decimal("50.00"), Decimal("89.99")])
        for line in lines:
            self.assertEqual(line.posted_net, line.net_amount())

    def test_the_document_records_its_total(self):
        acme, bolt = self.book()
        for invoice in (acme, bolt, self.credit_note):
            invoice.refresh_from_db()
            self.assertEqual(invoice.posted_total, invoice.total())
        self.assertEqual((acme.posted_total, self.credit_note.posted_total, bolt.posted_total),
                         (Decimal("212.98"), Decimal("39.99"), Decimal("24.00")))

    def test_a_draft_records_nothing(self):
        draft = Invoice.objects.create(customer=self.customer, invoice_date=MARCH,
                                       receivable_account=self.ar, currency=self.usd)
        line = self.line(draft, "1", "10")
        line.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual((line.posted_net, draft.posted_total), (None, None))


class RevenueFromRecordedFiguresTests(RevenueTestCase):
    def test_by_customer(self):
        self.book()
        self.assertEqual(self.report("customer"), {
            str(self.customer): (Decimal("154.99"), Decimal("18.00"), Decimal("6")),
            str(self.bolt): (Decimal("20.00"), Decimal("4.00"), Decimal("2")),
        })

    def test_by_item_a_charge_under_its_name_and_a_bare_line_under_its_description(self):
        self.book()
        self.assertEqual(self.report("item"), {
            str(self.item): (Decimal("109.99"), Decimal("22.00"), Decimal("5")),
            "Shipping": (Decimal("50.00"), Decimal("0"), Decimal("1")),
            "Pallet hire": (Decimal("15.00"), Decimal("0"), Decimal("2")),
        })

    def test_by_month_the_credit_note_in_the_month_it_was_raised(self):
        self.book()
        raised = self.credit_note.invoice_date.strftime("%Y-%m")
        expected = {
            "2026-03": (Decimal("188.32"), Decimal("24.66"), Decimal("7")),
            "2026-04": (Decimal("20.00"), Decimal("4.00"), Decimal("2")),
        }
        net, tax, quantity = expected.get(raised, (Decimal("0"),) * 3)
        expected[raised] = (net - Decimal("33.33"), tax - Decimal("6.66"), quantity - 1)
        self.assertEqual(self.report("month"), expected)

    def test_the_same_answer_worked_out_from_the_lines(self):
        """Documents posted before the figures were recorded are worked out
        as they always were; the two ways must not disagree by a paisa."""
        self.book()
        recorded = {group: self.report(group) for group in ("customer", "item", "month")}
        InvoiceLine.objects.update(posted_net=None)
        self.assertEqual({group: self.report(group) for group in recorded}, recorded)
        Invoice.objects.filter(customer=self.bolt).update(taxes_recorded=False)
        self.assertEqual({group: self.report(group) for group in recorded}, recorded)

    def test_half_recorded_counts_every_line_once(self):
        acme, _ = self.book()
        recorded = self.report("item")
        InvoiceLine.objects.filter(invoice=acme, charge__isnull=True).update(posted_net=None)
        self.assertEqual(self.report("item"), recorded)

    def test_an_unknown_grouping_is_refused_even_with_nothing_to_report(self):
        with self.assertRaises(ValueError):
            revenue_report(group_by="colour")


class FillingInTests(RevenueTestCase):
    def test_the_command_fills_what_is_missing_and_only_that(self):
        acme, bolt = self.book()
        InvoiceLine.objects.update(posted_net=None)
        Invoice.objects.update(posted_total=None)
        draft = Invoice.objects.create(customer=self.customer, invoice_date=MARCH,
                                       receivable_account=self.ar, currency=self.usd)
        self.line(draft, "1", "10")

        out = StringIO()
        call_command("record_posted_totals", stdout=out)
        self.assertIn("Invoice: recorded 3.", out.getvalue())
        self.assertIn("InvoiceLine: recorded 6.", out.getvalue())
        for invoice in (acme, bolt, self.credit_note):
            invoice.refresh_from_db()
            self.assertEqual(invoice.posted_total, invoice.total())
            for line in invoice.lines.all():
                self.assertEqual(line.posted_net, line.net_amount())
        self.assertEqual(list(draft.lines.values_list("posted_net", flat=True)), [None])
        draft.refresh_from_db()
        self.assertIsNone(draft.posted_total)

        again = StringIO()
        call_command("record_posted_totals", stdout=again)
        self.assertIn("Invoice: recorded 0.", again.getvalue())
        self.assertIn("InvoiceLine: recorded 0.", again.getvalue())


class SettledThenUnsettledTests(SalesTestCase):
    """
    A report skips a document its standing payments cover. Voiding the
    payment, the reverse path, must bring the document back, or a bounced
    cheque leaves an invoice that is owed missing from aging.
    """

    def setUp(self):
        super().setUp()
        self.vendor = Party.objects.create(code="V-1", name="Sack Co", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.payable = Account.objects.create(code="2100", name="AP",
                                              account_type=AccountType.LIABILITY)

    def pay(self, party, direction, amount, account):
        payment = Payment.objects.create(party=party, direction=direction, payment_date=MARCH,
                                         amount=Decimal(amount), currency=self.usd,
                                         bank_account=self.bank, counterpart_account=account)
        payment.post()
        return payment

    def open_invoices(self):
        return set(invoices_not_paid_in_full(Invoice.objects.filter(posted=True)))

    def open_bills(self):
        return set(bills_not_paid_in_full(Bill.objects.filter(posted=True)))

    def test_an_invoice_paid_part_stays_paid_in_full_goes_voided_comes_back(self):
        invoice = self.bill(self.make_order("10", "100"))
        part = self.pay(self.customer, "receipt", "400", self.ar)
        InvoicePayment.objects.create(invoice=invoice, payment=part, amount=Decimal("400"))
        self.assertEqual(self.open_invoices(), {invoice})

        rest = self.pay(self.customer, "receipt", "600", self.ar)
        InvoicePayment.objects.create(invoice=invoice, payment=rest, amount=Decimal("600"))
        self.assertEqual(self.open_invoices(), set())

        rest.void()
        self.assertEqual(self.open_invoices(), {invoice})
        self.assertEqual(invoice.amount_due(), Decimal("600.00"))

    def test_a_bill_paid_part_stays_paid_in_full_goes_voided_comes_back(self):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=MARCH,
                                             currency=self.usd)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                         quantity=Decimal("10"), unit_price=Decimal("10"))
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=MARCH)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(),
                                        warehouse=self.warehouse,
                                        quantity_received=Decimal("10"))
        receipt.post()
        bill = order.create_bill(self.payable, bill_date=MARCH)
        bill.post()
        bill.refresh_from_db()
        self.assertEqual(bill.posted_total, Decimal("100.00"))
        self.assertEqual(BillLine.objects.get(bill=bill).posted_net, Decimal("100.00"))

        part = self.pay(self.vendor, "disbursement", "40", self.payable)
        BillPayment.objects.create(bill=bill, payment=part, amount=Decimal("40"))
        self.assertEqual(self.open_bills(), {bill})

        rest = self.pay(self.vendor, "disbursement", "60", self.payable)
        BillPayment.objects.create(bill=bill, payment=rest, amount=Decimal("60"))
        self.assertEqual(self.open_bills(), set())

        rest.void()
        self.assertEqual(self.open_bills(), {bill})
        self.assertEqual(bill.amount_due(), Decimal("60.00"))

    def test_a_payment_whose_entry_was_reversed_no_longer_settles(self):
        """Reversing the payment's entry by hand is a void in all but name
        (Payment.is_voided says so); the invoice is owed again."""
        invoice = self.bill(self.make_order("10", "100"))
        payment = self.pay(self.customer, "receipt", "1000", self.ar)
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("1000"))
        self.assertEqual(self.open_invoices(), set())

        payment.journal_entry.create_reversal(memo="Bank recalled it")
        self.assertTrue(payment.is_voided())
        self.assertEqual(self.open_invoices(), {invoice})

    def test_a_document_posted_before_totals_were_recorded_is_never_skipped(self):
        invoice = self.bill(self.make_order("10", "100"))
        payment = self.pay(self.customer, "receipt", "1000", self.ar)
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("1000"))
        Invoice.objects.filter(pk=invoice.pk).update(posted_total=None)
        self.assertEqual(self.open_invoices(), {invoice})
