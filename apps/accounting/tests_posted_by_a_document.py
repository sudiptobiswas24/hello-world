"""
What a document posted is changed only through the document.

  An invoice's entry, its payment's, a delivery's, a goods receipt's: none
  is reversed from the journal, and the refusal names the document; the
  payment is still voided where it was made. An entry made by hand is
  still reversed from the journal. No line moves out of a posted entry
  into a draft, nor is deleted from one, and an entry read before it was
  posted is not deleted after. An entry made by hand cannot claim to
  reverse another, nor to come from a schedule.

Each was open. Reversed from the journal, an invoice read 1,000 owed
while receivables said nothing was, and its payment could no longer be
voided; a line moved out left the invoice's entry with one side.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounting.models import JournalEntry, JournalLine
from apps.accounting.recurring import RecurringJournal
from apps.purchasing.tests_lifecycle import PurchasingLifecycleTestCase
from apps.sales.tests_base import SalesTestCase

ENTRIES = "/api/accounting/journal-entries/"


def controller():
    client = APIClient()
    client.force_authenticate(User.objects.create_superuser("controller"))
    return client


class SalesSideTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.api = controller()

    def reverse(self, entry):
        return self.api.post(f"{ENTRIES}{entry.pk}/reverse/", {}, format="json")

    def test_an_invoice_and_its_payment_are_corrected_where_they_were_made(self):
        invoice = self.bill(self.make_order("10", "100"))
        payment = self.receipt("1000")
        self.allocate(payment, invoice, "1000")
        for entry, document in ((invoice.journal_entry, "invoice"), (payment.journal_entry, "payment")):
            with self.subTest(document=document):
                refused = self.reverse(entry)
                self.assertEqual(refused.status_code, 400)
                self.assertIn(f"was posted by {document}", refused.json()[0])
        self.assertFalse(JournalEntry.objects.filter(reverses__isnull=False).exists())
        payment.void(memo="Returned unpaid", on_date=datetime.date(2026, 3, 20))
        self.assertEqual(invoice.amount_due(), Decimal("1000.00"))
        refused = self.reverse(payment.voided_entry)
        self.assertEqual(refused.status_code, 400, "un-voided from the journal")

    def test_an_entrys_page_says_which_document_keeps_it_and_a_list_does_not_ask(self):
        invoice = self.bill(self.make_order("10", "100"))
        page = self.api.get(f"{ENTRIES}{invoice.journal_entry_id}/").json()
        self.assertEqual(page["posted_by"], f"Invoice {invoice}")
        listed = self.api.get(ENTRIES).json()
        self.assertEqual({row["posted_by"] for row in listed}, {None})

    def test_an_entry_made_by_hand_is_still_reversed_from_the_journal(self):
        made = self.api.post(ENTRIES, {"date": "2026-03-02", "memo": "Accrued rent"}, format="json").json()
        entry = JournalEntry.objects.get(pk=made["id"])
        JournalLine.objects.create(entry=entry, account=self.revenue, debit=Decimal("10"))
        JournalLine.objects.create(entry=entry, account=self.bank, credit=Decimal("10"))
        entry.post()
        reversed_ = self.reverse(entry)
        self.assertEqual(reversed_.status_code, 200)
        # Its reversal is not reversed in turn, as the screen never offered: the rent is posted again.
        refused = self.reverse(JournalEntry.objects.get(pk=reversed_.json()["id"]))
        self.assertEqual(refused.status_code, 400)
        self.assertIn(f"reverses JE-{entry.pk}", refused.json()[0])

    def test_a_delivery_keeps_its_entry_and_is_returned_not_reversed(self):
        delivery = self.ship(self.make_order("10", "100"), "10")
        self.assertEqual(delivery.journal_entry.lines.get(account=self.cogs).debit, Decimal("40.00"))
        with self.assertRaisesMessage(ValidationError, f"was posted by delivery {delivery}"):
            delivery.journal_entry.reverse_by_hand()

    def test_no_line_leaves_a_posted_entry(self):
        invoice = self.bill(self.make_order("10", "100"))
        posted = invoice.journal_entry
        draft = self.api.post(ENTRIES, {"date": "2026-03-02", "memo": "Mine"}, format="json").json()
        line = posted.lines.first()
        moved = self.api.patch(f"/api/accounting/journal-lines/{line.pk}/", {"entry": draft["id"]}, format="json")
        self.assertEqual(moved.status_code, 400)
        self.assertEqual((posted.lines.count(), posted.is_balanced()), (2, True))
        read_as_a_draft = JournalEntry.objects.create(date=datetime.date(2026, 3, 2), memo="Draft")
        JournalLine.objects.create(entry=read_as_a_draft, account=self.revenue, debit=Decimal("5"))
        JournalLine.objects.create(entry=read_as_a_draft, account=self.bank, credit=Decimal("5"))
        JournalEntry.objects.get(pk=read_as_a_draft.pk).post()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            read_as_a_draft.delete()
        self.assertEqual(JournalLine.objects.filter(entry=read_as_a_draft).count(), 2)

    def test_an_entry_made_by_hand_claims_nothing_it_did_not_do(self):
        payment = self.receipt("1000")
        rent = RecurringJournal.objects.create(code="RENT", memo="Rent", start_date=datetime.date(2026, 1, 31))
        made = self.api.post(ENTRIES, {"date": "2026-03-12", "memo": "Claims a reversal and a schedule",
                                       "reverses": payment.journal_entry_id, "recurring_journal": rent.pk},
                             format="json")
        self.assertEqual(made.status_code, 201, made.content)
        stored = JournalEntry.objects.get(pk=made.json()["id"])
        self.assertEqual((stored.reverses_id, stored.recurring_journal_id), (None, None))
        payment.void(memo="Returned unpaid", on_date=datetime.date(2026, 3, 20))


class PurchasingSideTests(PurchasingLifecycleTestCase):
    def test_a_goods_receipt_keeps_its_entry_and_is_returned_not_reversed(self):
        receipt = self.receive(self.make_order("10", "5"), "10")
        self.assertEqual(receipt.journal_entry.lines.get(account=self.inventory).debit, Decimal("50.00"))
        refused = controller().post(f"{ENTRIES}{receipt.journal_entry_id}/reverse/", {}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn(f"was posted by goods receipt {receipt}", refused.json()[0])
