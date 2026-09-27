from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Company

from apps.accounting.gst import GstSettings
from apps.accounting.models import (
    Account,
    AccountType,
    FiscalPosition,
    FiscalPositionTaxMapping,
    PartyTaxProfile,
    Tax,
)

from .models import Invoice, InvoiceLine
from .tests_audit_fixes import AuditTestCase


class GstSalesTestCase(AuditTestCase):
    def setUp(self):
        super().setUp()

        def tax(code, rate, account_code):
            account = Account.objects.create(
                code=account_code, name=f"Output {code}", account_type=AccountType.LIABILITY
            )
            return Tax.objects.create(
                code=code, name=code, rate=Decimal(rate), gst_head=code[:4].lower(),
                collected_account=account, paid_account=account,
            )

        self.cgst = tax("CGST9", "9", "2201")
        self.sgst = tax("SGST9", "9", "2202")
        self.igst = tax("IGST18", "18", "2203")
        interstate = FiscalPosition.objects.create(code="INTER", name="Inter-state")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=interstate, source_tax=self.cgst, target_tax=self.igst
        )
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=interstate, source_tax=self.sgst, target_tax=None
        )
        GstSettings.objects.create(gstin="27AABCD1234E1Z8", interstate_position=interstate)

    def posted(self, invoice):
        return {
            line.account.code: line.credit - line.debit
            for line in invoice.journal_entry.lines.all()
        }

    def balance(self, code):
        from apps.accounting.models import JournalLine

        return sum(
            (line.credit - line.debit for line in JournalLine.objects.filter(
                account__code=code, entry__posted=True
            )),
            Decimal("0"),
        )


class GstInvoiceTests(GstSalesTestCase):
    """The state line, carried through to what an invoice posts."""

    def test_inside_the_state_central_and_state_tax_each_to_its_own_account(self):
        PartyTaxProfile.objects.create(party=self.customer, gstin="27AABCD1234E2Z7")
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])

        posted = self.posted(invoice)
        self.assertEqual(posted["2201"], Decimal("90.00"))
        self.assertEqual(posted["2202"], Decimal("90.00"))
        self.assertNotIn("2203", posted)
        self.assertEqual(posted["1100"], Decimal("-1180.00"))

    def test_across_a_state_line_integrated_tax_at_the_whole_rate(self):
        PartyTaxProfile.objects.create(party=self.customer, gstin="29AABCE5678F1ZD")
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])

        posted = self.posted(invoice)
        self.assertEqual(posted["2203"], Decimal("180.00"))
        self.assertNotIn("2201", posted)
        self.assertNotIn("2202", posted)
        self.assertEqual(posted["1100"], Decimal("-1180.00"))

    def test_a_customer_nobody_has_placed_cannot_be_invoiced_tax(self):
        """Defaulting to the intra-state pair is the plausible wrong answer."""
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst], post=False)
        with self.assertRaisesMessage(ValidationError, "no tax profile"):
            invoice.tax_total()

    def test_an_untaxed_line_needs_no_place(self):
        invoice = self.make_invoice("1000", post=False)
        self.assertEqual(invoice.tax_total(), Decimal("0"))


class RecordedTaxTests(GstSalesTestCase):
    """What a posted invoice bore is a fact, not a recomputation."""

    def setUp(self):
        super().setUp()
        self.profile = PartyTaxProfile.objects.create(
            party=self.customer, gstin="27AABCD1234E2Z7"
        )
        self.item.hsn_code = "63053300"
        self.item.save()

    def move_customer_to_karnataka(self):
        self.profile.gstin = "29AABCE5678F1ZD"
        self.profile.gst_state = ""
        self.profile.save()

    def test_posting_writes_down_each_tax_and_the_party_as_it_stood(self):
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])
        line = invoice.lines.get()

        rows = [(r.tax.code, r.rate, r.gst_head, r.taxable, r.amount)
                for r in line.recorded_taxes.all()]
        self.assertEqual(rows, [
            ("CGST9", Decimal("9"), "cgst", Decimal("1000.00"), Decimal("90.00")),
            ("SGST9", Decimal("9"), "sgst", Decimal("1000.00"), Decimal("90.00")),
        ])
        invoice.refresh_from_db()
        self.assertTrue(invoice.taxes_recorded)
        self.assertEqual(invoice.party_gstin, "27AABCD1234E2Z7")
        self.assertEqual(invoice.place_of_supply, "27")
        line.refresh_from_db()
        self.assertEqual(line.hsn_code, "63053300")

    def test_a_posted_invoice_does_not_change_when_the_customer_moves(self):
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])
        self.move_customer_to_karnataka()

        invoice = type(invoice).objects.get(pk=invoice.pk)
        self.assertEqual(invoice.tax_total(), Decimal("180.00"))
        self.assertEqual(
            [tax.code for tax in invoice.lines.get().effective_taxes()], ["CGST9", "SGST9"]
        )

    def test_a_credit_note_reverses_what_was_charged_not_what_would_be_now(self):
        """The defect: the credit note re-derived its taxes from the
        customer as it now stood, and reversed IGST that was never charged
        while the CGST and SGST stayed on the books."""
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])
        self.move_customer_to_karnataka()

        note = invoice.create_credit_note()

        self.assertEqual(self.balance("2201"), Decimal("0"))
        self.assertEqual(self.balance("2202"), Decimal("0"))
        self.assertEqual(self.balance("2203"), Decimal("0"))
        self.assertEqual(self.balance("1100"), Decimal("0"))
        self.assertEqual(note.party_gstin, "27AABCD1234E2Z7")
        self.assertEqual(note.place_of_supply, "27")

    def test_a_rate_changed_since_does_not_reach_the_credit_note(self):
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])
        type(self.cgst).objects.filter(pk=self.cgst.pk).update(rate=Decimal("6"))

        invoice.create_credit_note()
        self.assertEqual(self.balance("2201"), Decimal("0"))

    def test_partial_credits_reverse_exactly_what_was_charged(self):
        """Three credits of one unit each: proportional rounding alone
        gives 0.90 three times against 2.71 charged."""
        invoice = self.make_invoice("10.05", post=False)
        line = invoice.lines.get()
        type(line).objects.filter(pk=line.pk).update(quantity=Decimal("3"))
        line.refresh_from_db()
        line.taxes.set([self.cgst])
        invoice.post()
        self.assertEqual(self.balance("2201"), Decimal("2.71"))

        credited = []
        for _ in range(3):
            note = invoice.create_credit_note(quantities={line: Decimal("1")})
            credited.append(note.tax_total())
        self.assertEqual(credited, [Decimal("0.90"), Decimal("0.90"), Decimal("0.91")])
        self.assertEqual(self.balance("2201"), Decimal("0"))

    def test_a_price_adjustment_reverses_its_share_not_the_whole_tax(self):
        """Every unit credited at a twentieth of the price: by quantity
        this looked like the last of the line and reversed all 90."""
        invoice = self.make_invoice("100", post=False)
        line = invoice.lines.get()
        type(line).objects.filter(pk=line.pk).update(quantity=Decimal("10"))
        line.taxes.set([self.cgst])
        invoice.post()

        note = Invoice.objects.create(
            customer=self.customer, invoice_date=invoice.invoice_date,
            receivable_account=self.ar, currency=self.usd, credits=invoice,
        )
        InvoiceLine.objects.create(
            invoice=note, credits_line=line, item=self.item, quantity=Decimal("10"),
            unit_price=Decimal("5"), revenue_account=self.revenue,
        )
        self.assertEqual(note.tax_total(), Decimal("4.50"))

    def test_under_document_rounding_the_credit_is_not_re_rounded(self):
        Company.objects.update(tax_rounding="document")
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])
        self.move_customer_to_karnataka()
        invoice.create_credit_note()
        self.assertEqual(self.balance("2201"), Decimal("0"))
        self.assertEqual(self.balance("2203"), Decimal("0"))

    def test_the_hsn_code_is_the_one_it_posted_with(self):
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])
        self.item.hsn_code = "39269099"
        self.item.save()

        note = invoice.create_credit_note()
        self.assertEqual(invoice.lines.get().hsn_code, "63053300")
        self.assertEqual(note.lines.get().hsn_code, "63053300")

    def test_a_tax_with_no_gst_head_cannot_post_under_gst(self):
        type(self.cgst).objects.filter(pk=self.cgst.pk).update(gst_head="")
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst], post=False)

        with self.assertRaisesMessage(ValidationError, "no GST head"):
            invoice.post()
        invoice = type(invoice).objects.get(pk=invoice.pk)
        self.assertFalse(invoice.posted)
        self.assertFalse(invoice.taxes_recorded)
        self.assertFalse(invoice.lines.get().recorded_taxes.exists())
        self.assertEqual(self.balance("2202"), Decimal("0"))

    def test_a_failed_posting_can_be_retried_on_the_same_object(self):
        """Recording last means a failure leaves nothing claiming to be
        recorded — a retry must not post with no tax at all."""
        type(self.cgst).objects.filter(pk=self.cgst.pk).update(gst_head="")
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst], post=False)
        with self.assertRaises(ValidationError):
            invoice.post()
        type(self.cgst).objects.filter(pk=self.cgst.pk).update(gst_head="cgst")
        invoice.post()
        self.assertEqual(self.balance("2201"), Decimal("90.00"))

    def test_a_recorded_tax_cannot_be_edited(self):
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])
        row = invoice.lines.get().recorded_taxes.first()
        row.amount = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "is a fact"):
            row.save()

    def test_a_recorded_tax_is_never_negative(self):
        from django.db import IntegrityError, transaction

        from .models import InvoiceLineTax

        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])
        with self.assertRaises(IntegrityError), transaction.atomic():
            InvoiceLineTax.objects.create(
                line=invoice.lines.get(), tax=self.cgst, rate=Decimal("9"),
                taxable=Decimal("100"), amount=Decimal("-9"),
            )

    def test_an_invoice_posted_before_recording_existed_still_computes(self):
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])
        line = invoice.lines.get()
        line.recorded_taxes.all().delete()
        type(invoice).objects.filter(pk=invoice.pk).update(taxes_recorded=False)

        invoice = type(invoice).objects.get(pk=invoice.pk)
        self.assertEqual(invoice.tax_total(), Decimal("180.00"))
        invoice.create_credit_note()
        self.assertEqual(self.balance("2201"), Decimal("0"))

    def test_a_charge_line_records_its_sac(self):
        from apps.accounting.models import ChargeType

        freight = ChargeType.objects.create(
            code="FRT", name="Freight", revenue_account=self.revenue, hsn_code="9965"
        )
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst], post=False)
        line = InvoiceLine.objects.create(
            invoice=invoice, charge=freight, quantity=Decimal("1"),
            unit_price=Decimal("50"), revenue_account=self.revenue,
        )
        line.taxes.set([self.cgst, self.sgst])
        invoice.post()

        line.refresh_from_db()
        self.assertEqual(line.hsn_code, "9965")

    def test_a_mixed_note_rounds_its_own_lines_without_the_bound_ones(self):
        """A credit note carrying a line bound to the invoice beside a free
        adjustment line: under document rounding the free line is rounded
        on its own. Pooled with the bound line, 100.05 at 9% took 9.01."""
        Company.objects.update(tax_rounding="document")
        invoice = self.make_invoice("10.05", post=False)
        original = invoice.lines.get()
        type(original).objects.filter(pk=original.pk).update(quantity=Decimal("3"))
        original.taxes.set([self.cgst])
        invoice.post()

        note = Invoice.objects.create(
            customer=self.customer, invoice_date=invoice.invoice_date,
            receivable_account=self.ar, currency=self.usd, credits=invoice,
        )
        bound = InvoiceLine.objects.create(
            invoice=note, credits_line=original, item=self.item, quantity=Decimal("1"),
            unit_price=Decimal("10.05"), revenue_account=self.revenue,
        )
        free = InvoiceLine.objects.create(
            invoice=note, item=self.item, quantity=Decimal("1"),
            unit_price=Decimal("100.05"), revenue_account=self.revenue,
        )
        free.taxes.set([self.cgst])

        amounts = note.line_tax_amounts()
        self.assertEqual(amounts[bound], [(self.cgst, Decimal("0.90"))])
        self.assertEqual(amounts[free], [(self.cgst, Decimal("9.00"))])

    def test_the_registration_is_frozen_with_the_document(self):
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])
        self.profile.gst_registration = "sez"
        self.profile.save()

        note = invoice.create_credit_note()
        invoice.refresh_from_db()
        self.assertEqual(invoice.party_registration, "regular")
        self.assertEqual(note.party_registration, "regular")


class OverseasTests(GstSalesTestCase):
    def test_an_overseas_customer_is_supplied_outside_india(self):
        PartyTaxProfile.objects.create(party=self.customer, gst_registration="overseas")
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])

        self.assertEqual(invoice.place_of_supply, "96")
        self.assertEqual(invoice.party_registration, "overseas")
        # With no export position of its own, an export pays IGST.
        self.assertEqual(self.posted(invoice)["2203"], Decimal("180.00"))


class RecordedTaxWithoutGstTests(AuditTestCase):
    """The same defect with no GST at all: an export position given to
    the customer after the invoice zero-rated its credit note."""

    def test_a_fiscal_position_given_later_does_not_reach_the_credit_note(self):
        invoice = self.make_invoice("100", taxes=[self.vat])
        export = FiscalPosition.objects.create(code="EXPORT", name="Export")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=export, source_tax=self.vat, target_tax=self.zero_rated
        )
        PartyTaxProfile.objects.create(party=self.customer, fiscal_position=export)

        note = invoice.create_credit_note()
        self.assertEqual(note.tax_total(), Decimal("20.00"))
        self.assertEqual(
            sum((l.credit - l.debit for l in invoice.journal_entry.lines.filter(
                account=self.tax_payable)), Decimal("0"))
            + sum((l.credit - l.debit for l in note.journal_entry.lines.filter(
                account=self.tax_payable)), Decimal("0")),
            Decimal("0"),
        )
