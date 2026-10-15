"""
review_acct probes: facts a posted TCS document must keep, and what a cancelled invoice still lets through.
A failure is a finding. Not part of the product's suite.
"""

import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import PaymentTerms
from apps.gst.ewaybill import prepare as prepare_eway
from apps.sales.tcs import TcsChallan
from apps.sales.tests_tcs import OCTOBER, TcsTestCase

SEP = lambda d: datetime.date(2026, 9, 1 + d - 1)  # noqa: E731


class KeptFacts(TcsTestCase):
    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True)
        return sum((r.credit - r.debit for r in rows), D("0"))

    def test_the_payable_account_a_section_collected_into_is_where_the_challan_and_the_give_back_go(self):
        """Shape 11, 'a setting read live where a fact should be kept': the section's account moved after the collection."""
        invoice = self.t1()
        newer = Account.objects.create(code="2291", name="TCS payable (new)", account_type=AccountType.LIABILITY)
        self.section.payable_account = newer
        self.section.save()
        self.credit(invoice, "500", SEP(20))
        TcsChallan.pay(self.section, "2026-09-01", OCTOBER, self.bank, "00001", "0510308")
        self.assertEqual((self.balance(self.tcs_payable), self.balance(newer)), (D("0"), D("0")),
                         "collected into one account, given back and paid over out of another")

    def test_a_settlement_discount_is_not_taken_on_the_tcs_collected(self):
        terms = PaymentTerms.objects.create(code="2/10", name="2/10 net 30", net_days=30, discount_percent=D("2"), discount_days=10)
        invoice = self.t1(post=False)
        invoice.payment_terms = terms
        invoice.save()
        invoice.post()
        self.assertEqual(invoice.settlement_discount(), D("2124.00"), "2% of the 106,200.00 supply; TCS of 1,062.00 is not discounted")

    def test_no_e_way_bill_is_prepared_on_a_cancelled_invoice(self):
        invoice = self.invoice(self.mh, [(self.sack, "1000", "125", "0", self.pair)])
        record = self.registered(invoice)
        record.cancellation("2", "Wrong rate")
        record.record_cancellation(timezone.now())
        invoice.refresh_from_db()
        with self.assertRaises(ValidationError):
            prepare_eway(invoice, vehicle_number="MH12AB1234", distance_km=100)

    def test_a_cancelled_invoice_is_not_credited_for_a_part_by_quantity(self):
        invoice = self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)])
        record = self.registered(invoice)
        record.cancellation("2", "x")
        record.record_cancellation(timezone.now())
        invoice.refresh_from_db()
        with self.assertRaises(ValidationError):
            invoice.create_credit_note(quantities={invoice.lines.get(): D("10")})

    def test_dunning_aging_and_statement_show_a_cancelled_invoice_as_nothing_due(self):
        invoice = self.t1()
        record = self.registered(invoice)
        record.cancellation("2", "x")
        record.record_cancellation(timezone.now())
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_due(), D("0"))
        self.assertEqual(self.mh.__class__.objects.get(pk=self.mh.pk).pk, self.mh.pk)


class OtherDocumentsWhoseIrnIsCancelled(TcsTestCase):
    def test_a_credit_notes_irn_is_not_cancelled_where_the_books_cannot_follow(self):
        """Review F2: the books cannot take a credit note back, so its cancellation request is never built."""
        invoice = self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)])
        note = invoice.create_credit_note(quantities={invoice.lines.get(): D("10")})
        record = self.registered(note)
        with self.assertRaisesMessage(ValidationError, "is a credit note. Its IRN is not cancelled from here"):
            record.cancellation("2", "Wrong quantity")
        record.refresh_from_db()
        self.assertIsNone(record.cancel_payload)
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_due(), D("15930.00"))

    def test_cancelling_an_export_in_dollars_reverses_it_at_its_own_rate_and_keeps_its_lut(self):
        invoice = self.export([(self.sack, "1000", "1.37", "0", self.pair)])
        record = self.registered(invoice)
        record.cancellation("1", "Duplicate")
        note = record.record_cancellation(timezone.now())
        self.assertEqual((self.ledger("1100"), self.ledger("4000")), (D("0"), D("0")))
        self.assertEqual(note.issued_as["lut"]["arn"], "AD2703260012345")
        self.assertEqual(note.exchange_rate, invoice.exchange_rate)


class MoreOfTheLaw(TcsTestCase):
    def test_form_127_without_the_buyers_pan_exempts_nothing(self):
        """Research notes (Form 127 FAQs): 'PAN mandatory or the seller collects'. Review F1."""
        from apps.accounting.models import TcsDeclaration

        nopan = self.party("NOPAN3")
        self.address(nopan, "4 Station Road", "Nashik", "Maharashtra", "422001")
        terms = dict(party=nopan, section=self.section, reference="F127/1", received_on=datetime.date(2026, 4, 2),
                     valid_from=datetime.date(2026, 4, 1), valid_to=datetime.date(2027, 3, 31))
        with self.assertRaisesMessage(ValidationError, "exempts a buyer only with its PAN on it"):
            TcsDeclaration.objects.create(**terms)
        with self.assertRaisesMessage(ValidationError, "is not one"):
            TcsDeclaration.objects.create(pan="AB12", **terms)
        # One recorded before the PAN was asked for is not honoured as the invoice posts.
        TcsDeclaration.objects.bulk_create([TcsDeclaration(**terms)])
        self.assertEqual(self.t1(nopan).tcs_amount, D("5310.00"), "a declaration with no PAN behind it exempted the sale")

    def test_the_collections_api_says_a_voided_challan_has_not_paid_a_row_over(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        self.t1()
        challan = TcsChallan.pay(self.section, "2026-09-01", OCTOBER, self.bank, "00001", "0510308")
        challan.void()
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("root", "r@example.com", "x"))
        (row,) = client.get("/api/sales/tcs-collections/").json()
        self.assertIsNone(row["challan"], "the list says the row is on a challan that was voided; the screen reads that as 'Paid over'")

    def test_an_export_invoice_names_the_country_of_destination(self):
        from apps.gst.tests_tax_invoice import printed_text
        from apps.sales.documents import render_invoice_pdf

        invoice = self.export([(self.sack, "1000", "1.37", "0", self.pair)])
        text = " ".join(" ".join(printed_text(render_invoice_pdf, invoice)).split())
        self.assertIn("United Arab Emirates", text)
