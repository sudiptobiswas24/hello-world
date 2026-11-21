"""
A credit note with GST against an invoice the old system issued.

The old invoice is in this system only as what was still owed on it (an
opening balance, no tax of its own). A rate difference or a return on it
after go-live is a credit note under GST all the same: it carries its
own lines and tax, reduces the output tax this month, and is reported in
GSTR-1 against the old invoice's number and date. A plain credit of the
opening balance (writing off what was owed) is not a supply and stays
out, as before.

Worked by hand (r10_numbers.py): OLD/1 owed 118,000.00. A rate
difference of 1 x 10,000.00 at 9% + 9% is 900.00 + 900.00, 11,800.00 in
all, leaving 106,200.00 owed. Inter-state to an unregistered buyer,
3,000.00 at 18% IGST is 540.00, 3,540.00 in all.
"""

import datetime

from django.core.exceptions import ValidationError

from apps.purchasing.models import Bill, BillLine
from apps.sales.models import Invoice, InvoiceLine

from .einvoice import build
from .returns import gstr1, gstr3b, month
from .tests import DAY, SEPTEMBER, D, GstReturnTestCase, gstin
from .tests_einvoice import EInvoiceTestCase

OCTOBER = month("2026-10")
# A day that has come: a correction is not dated ahead (correction_date, O163); 12 October was,
# until that day, and passed only because nothing asked.
LATER = DAY.replace(month=10, day=1)


class OldSupplyTestCase(GstReturnTestCase):
    def setUp(self):
        super().setUp()
        self.buyer = self.party("B2B", gstin=gstin("27AAACB1234K1Z"), gst_state="27",
                                gst_registration="regular")
        self.walk_in = self.party("B2C", gst_state="29", gst_registration="unregistered")
        self.vendor = self.party("VEN", role="vendor", gstin=gstin("27AAACV1234K1Z"),
                                 gst_state="27", gst_registration="regular")

    def opening(self, customer=None, amount="118000", reference="OLD/1"):
        invoice = Invoice.objects.create(customer=customer or self.buyer, invoice_date=DAY,
                                         reference=reference, receivable_account=self.ar,
                                         currency=self.inr, is_opening_balance=True)
        InvoiceLine.objects.create(invoice=invoice, description=f"Opening balance: {reference}",
                                   quantity=D("1"), unit_price=D(amount), revenue_account=self.revenue)
        invoice.post()
        return invoice

    def rate_difference(self, invoice, price="10000", taxes=None, **kwargs):
        return invoice.credit_old_supply(
            [{"description": "Rate difference on OLD/1", "item": self.sack, "quantity": D("1"),
              "unit_price": D(price), "taxes": self.pair if taxes is None else taxes,
              "revenue_account": self.revenue}],
            on_date=LATER, **kwargs)


class RefusalTests(OldSupplyTestCase):
    def test_only_an_invoice_from_the_old_system(self):
        current = self.sell(self.buyer, "1000")
        with self.assertRaisesMessage(ValidationError, "old system"):
            self.rate_difference(current)

    def test_not_a_draft(self):
        draft = Invoice.objects.create(customer=self.buyer, invoice_date=DAY, reference="OLD/2",
                                       receivable_account=self.ar, is_opening_balance=True)
        with self.assertRaisesMessage(ValidationError, "posted"):
            self.rate_difference(draft)

    def test_something_to_credit(self):
        with self.assertRaisesMessage(ValidationError, "Nothing to credit"):
            self.opening().credit_old_supply([], on_date=LATER)

    def test_an_unregistered_buyer_says_what_the_old_invoice_was_for(self):
        # Whether its note goes to CDNUR as a large one turns on it.
        invoice = self.opening(customer=self.walk_in, amount="3540")
        with self.assertRaisesMessage(ValidationError, "what the old invoice was for"):
            self.rate_difference(invoice, price="3000", taxes=[self.igst])

    def test_no_more_than_the_old_invoice_was_for(self):
        invoice = self.opening()
        self.rate_difference(invoice, old_value=D("118000"))
        with self.assertRaisesMessage(ValidationError, "106200.00"):
            self.rate_difference(invoice, price="100000", old_value=D("118000"))
        self.assertEqual(invoice.credit_notes.filter(posted=True).count(), 1)


class TheNoteTests(OldSupplyTestCase):
    def test_it_reduces_what_is_owed_and_the_output_tax(self):
        invoice = self.opening()
        note = self.rate_difference(invoice)
        self.assertEqual((note.total(), note.credits, note.invoice_date), (D("11800.00"), invoice, LATER))
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_due(), D("106200.00"))
        self.assertEqual((self.ledger("2201"), self.ledger("2202")), (D("-900.00"), D("-900.00")))

    def test_gstr1_reports_it_against_the_old_number_and_date(self):
        note = self.rate_difference(self.opening())
        [entry] = gstr1(*OCTOBER)["cdnr"]
        self.assertEqual((entry["number"], entry["original"], entry["original_date"], entry["value"]),
                         (note.number, "OLD/1", DAY, D("11800.00")))
        self.assertEqual(entry["items"], [{"rate": D("18"), "taxable": D("10000.00"), "igst": D("0"),
                                           "cgst": D("900.00"), "sgst": D("900.00"), "cess": D("0")}])
        three = gstr3b(*OCTOBER)
        self.assertEqual(three["3.1a"]["taxable"], D("-10000.00"))

    def test_a_plain_credit_of_the_balance_is_still_no_supply(self):
        invoice = self.opening()
        invoice.create_credit_note(memo="Settled in the old system")
        self.assertEqual(gstr1(*SEPTEMBER)["cdnr"], [])
        self.assertEqual(gstr1(*OCTOBER)["cdnr"], [])

    def test_an_unregistered_buyers_note_follows_the_old_value(self):
        # Inter-state, unregistered: a large old invoice's note is CDNUR,
        # a small one's is netted in the B2C summary.
        large = self.opening(customer=self.walk_in, amount="3540", reference="OLD/7")
        self.rate_difference(large, price="3000", taxes=[self.igst],
                             old_value=D("300000"))
        small = self.opening(customer=self.walk_in, amount="3540", reference="OLD/8")
        self.rate_difference(small, price="3000", taxes=[self.igst],
                             old_value=D("50000"))
        one = gstr1(*OCTOBER)
        self.assertEqual([(entry["original"], entry["type"]) for entry in one["cdnur"]],
                         [("OLD/7", "b2cl")])
        [row] = one["b2cs"]
        self.assertEqual((row["taxable"], row["igst"]), (D("-3000.00"), D("-540.00")))


class ItsEInvoiceTests(EInvoiceTestCase):
    def test_it_is_registered_against_the_old_number(self):
        invoice = Invoice.objects.create(customer=self.mh, invoice_date=DAY, reference="OLD/1",
                                         receivable_account=self.ar, currency=self.inr,
                                         is_opening_balance=True)
        InvoiceLine.objects.create(invoice=invoice, description="Opening balance: OLD/1",
                                   quantity=D("1"), unit_price=D("118000"), revenue_account=self.revenue)
        invoice.post()
        with self.assertRaisesMessage(ValidationError, "opening balance from the old system"):
            build(invoice)
        note = invoice.credit_old_supply(
            [{"description": "Rate difference on OLD/1", "item": self.sack, "quantity": D("1"),
              "unit_price": D("10000"), "taxes": self.pair, "revenue_account": self.revenue}],
            on_date=LATER)
        payload = build(note)
        self.assertEqual(payload["DocDtls"]["Typ"], "CRN")
        self.assertEqual(payload["RefDtls"]["PrecDocDtls"][0]["InvNo"], "OLD/1")
        self.assertEqual(payload["ValDtls"]["TotInvVal"], 11800.0)


class TheMirrorOnBillsTests(OldSupplyTestCase):
    """A debit note to a vendor on a bill the old system booked takes the input tax back."""

    def opening_bill(self):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=DAY, reference="V/9",
                                   payable_account=self.ap, is_opening_balance=True)
        BillLine.objects.create(bill=bill, description="Opening balance: V/9", quantity=D("1"),
                                unit_price=D("59000"), expense_account=self.expense)
        bill.post()
        return bill

    def test_it_reduces_what_is_owing_and_the_input_tax(self):
        bill = self.opening_bill()
        note = bill.debit_old_supply(
            [{"description": "Short-weight granules", "item": self.sack, "quantity": D("1"),
              "unit_price": D("5000"), "taxes": self.pair, "expense_account": self.expense}],
            on_date=LATER)
        self.assertEqual(note.total(), D("5900.00"))
        bill.refresh_from_db()
        self.assertEqual(bill.amount_due(), D("53100.00"))
        three = gstr3b(*OCTOBER)
        self.assertEqual((three["4A5"]["cgst"], three["4A5"]["sgst"]), (D("-450.00"), D("-450.00")))

    def test_neither_note_is_dated_before_what_it_corrects(self):
        # O163: credit_old_supply and debit_old_supply took any day.
        before = DAY - datetime.timedelta(days=1)
        with self.assertRaisesMessage(ValidationError, f"is not credited on {before}: it was invoiced on {DAY}"):
            self.opening().credit_old_supply(
                [{"description": "Rate difference on OLD/1", "item": self.sack, "quantity": D("1"),
                  "unit_price": D("10000"), "taxes": self.pair, "revenue_account": self.revenue}], on_date=before)
        bill = self.opening_bill()
        with self.assertRaisesMessage(ValidationError, f"is not debited on {before}"):
            bill.debit_old_supply(
                [{"description": "Short-weight granules", "item": self.sack, "quantity": D("1"),
                  "unit_price": D("5000"), "taxes": self.pair, "expense_account": self.expense}], on_date=before)

    def test_only_a_bill_from_the_old_system(self):
        current = self.buy(self.vendor, "1000")
        with self.assertRaisesMessage(ValidationError, "old system"):
            current.debit_old_supply(
                [{"description": "x", "quantity": D("1"), "unit_price": D("1"), "taxes": [],
                  "expense_account": self.expense}], on_date=LATER)


class OverTheApiTests(OldSupplyTestCase):
    def setUp(self):
        super().setUp()
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        from django.contrib.auth.models import Group, User
        from rest_framework.test import APIClient

        user = User.objects.create_user(role.replace(" ", "-").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def body(self, **line):
        return {"memo": "Rate difference", "on_date": LATER.isoformat(), "lines": [
            {"description": "Rate difference on OLD/1", "item": self.sack.pk, "quantity": "1",
             "unit_price": "10000", "taxes": [tax.pk for tax in self.pair],
             "revenue_account": self.revenue.pk} | line]}

    def test_accounts_post_one_and_it_reads_back(self):
        invoice = self.opening()
        response = self.as_("AR Manager").post(
            f"/api/sales/invoices/{invoice.pk}/credit_old_supply/", self.body(), format="json")
        self.assertEqual(response.status_code, 201, response.content)
        note = response.json()
        self.assertEqual((note["total"], note["credits"], note["corrects_old_supply"]),
                         ("11800.00", invoice.pk, True))
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_due(), D("106200.00"))

    def test_what_is_not_a_number_or_not_there_is_said(self):
        invoice = self.opening()
        client = self.as_("AR Manager")
        url = f"/api/sales/invoices/{invoice.pk}/credit_old_supply/"
        for line, said in (({"quantity": "lots"}, "quantity is a number"),
                           ({"taxes": [99999]}, "no such tax"),
                           ({"item": "abc"}, "no such item")):
            with self.subTest(line=line):
                response = client.post(url, self.body(**line), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(said, str(response.json()))
        self.assertFalse(invoice.credit_notes.exists())

    def test_a_rep_may_not(self):
        invoice = self.opening()
        response = self.as_("Sales Rep").post(
            f"/api/sales/invoices/{invoice.pk}/credit_old_supply/", self.body(), format="json")
        self.assertIn(response.status_code, (403, 404))
        self.assertFalse(invoice.credit_notes.exists())

    def test_the_vendor_side(self):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=DAY, reference="V/9",
                                   payable_account=self.ap, is_opening_balance=True)
        BillLine.objects.create(bill=bill, description="Opening balance: V/9", quantity=D("1"),
                                unit_price=D("59000"), expense_account=self.expense)
        bill.post()
        response = self.as_("AP Manager").post(f"/api/purchasing/bills/{bill.pk}/debit_old_supply/", {
            "lines": [{"description": "Short weight", "quantity": "1", "unit_price": "5000",
                       "taxes": [tax.pk for tax in self.pair], "expense_account": self.expense.pk}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["total"], "5900.00")
