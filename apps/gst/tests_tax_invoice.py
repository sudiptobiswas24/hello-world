"""
The printed tax invoice, field by field, against what the document posted
(CGST Rule 46; Rule 53 for a credit note). O218.

A Maharashtra plant (27), the e-invoice fixtures' figures
(calc_fix_acct/scenarios.py):

  P1  Maharashtra buyer, 100 kg at 150: 15,000.00, CGST 9% 1,350.00,
      SGST 9% 1,350.00, 17,700.00.
  P2  Karnataka buyer, inter-state: sacks 122,500.00 IGST 22,050.00,
      freight 3,000.00 IGST 540.00; taxable 125,500.00, IGST 22,590.00,
      148,090.00. Delivered to a second Karnataka site.
  P3  Export, 1,000 kg at 1.37 USD: 1,370.00. Under LUT no IGST; on
      payment, IGST 18% 246.60, 1,616.60.
  P4  Credit for 10 kg of P1: 1,500.00, CGST 135.00, SGST 135.00, 1,770.00.
"""

import re
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounting.gst import GstSettings
from apps.core.models import AddressType, Company
from apps.sales.documents import render_invoice_pdf, tax_invoice

from .tests import D, gstin
from .tests_einvoice import EInvoiceTestCase


def printed_text(render, *args, **kwargs):
    """
    The text a render puts on the page, line by line, for a test to read
    what was printed rather than only that a PDF came out. Rendered with
    the page streams uncompressed, so each line is the string it draws.
    """
    with mock.patch("reportlab.rl_config.pageCompression", 0):
        pdf = render(*args, **kwargs)
    found = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", pdf)
    unescape = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"(": b"(", b")": b")", b"\\": b"\\"}
    lines = [re.sub(rb"\\(.)", lambda m: unescape.get(m.group(1), m.group(1)), text) for text in found]
    return [line.decode("cp1252") for line in lines]


class TaxInvoiceTestCase(EInvoiceTestCase):
    def setUp(self):
        super().setUp()
        Company.objects.update(legal_name="Deccan Polysacks Private Limited")

    def p1(self):
        return self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)])

    def text(self, invoice, copy="original"):
        return " ".join(" ".join(printed_text(render_invoice_pdf, invoice, copy)).split())


class IntraStateTests(TaxInvoiceTestCase):
    def test_every_field_rule_46_asks_for(self):
        invoice = self.p1()
        printed = tax_invoice(invoice)
        self.assertEqual(printed["heading"], "Tax Invoice")
        self.assertEqual(printed["copy"], "Original for recipient")
        self.assertEqual((printed["number"], printed["date"]), (invoice.number, invoice.invoice_date))
        self.assertEqual(printed["supplier"], {
            "name": "Deccan Polysacks Private Limited", "trade_name": "Deccan Polysacks",
            "address": ["Plot 12, MIDC Chakan", "Pune Maharashtra 410501"],
            "gstin": self.settings.gstin, "state": "27", "tax_id": "", "state_name": "Maharashtra (27)"})
        self.assertEqual(printed["recipient"], {
            "name": "MH-REG", "address": ["3 Market Yard", "Nashik 27 422001"],
            "gstin": self.mh.tax_profile.gstin, "state": "27", "state_name": "Maharashtra (27)"})
        self.assertIsNone(printed["delivery"])
        self.assertEqual(printed["place_of_supply"], "Maharashtra (27)")
        self.assertEqual(printed["reverse_charge"], "No")
        (line,) = printed["lines"]
        self.assertEqual(
            (line["hsn"], line["quantity"], line["unit"], line["taxable"], line["taxes"], line["amount"]),
            ("63053300", D("100"), "kg", D("15000.00"),
             [("CGST", D("9"), D("1350.00")), ("SGST", D("9"), D("1350.00"))], D("17700.00")))
        self.assertEqual(printed["taxes"], [("CGST", D("9"), D("1350.00")), ("SGST", D("9"), D("1350.00"))])
        self.assertEqual(printed["taxable"], D("15000.00"))
        self.assertIsNone(printed["export"])
        self.assertEqual(printed["signatory"], "Deccan Polysacks Private Limited")

    def test_the_paper_prints_them(self):
        invoice = self.p1()
        text = self.text(invoice)
        for expected in ["Tax Invoice", "Original for recipient", invoice.number, "10 Sep 2026",
                         f"GSTIN {self.settings.gstin}", f"GSTIN {self.mh.tax_profile.gstin}",
                         "State Maharashtra (27)", "Place of supply", "Reverse charge", "63053300",
                         "100.00 kg", "15,000.00", "CGST 9% 1,350.00", "SGST 9% 1,350.00",
                         "CGST @ 9%", "SGST @ 9%", "17,700.00", "Plot 12, MIDC Chakan",
                         "3 Market Yard", "Authorised signatory",
                         "Total in words: Rupees seventeen thousand seven hundred only."]:
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        self.assertNotIn("IGST", text)

    def test_the_three_copies(self):
        invoice = self.p1()
        self.assertIn("Duplicate for transporter", self.text(invoice, "duplicate"))
        self.assertIn("Triplicate for supplier", self.text(invoice, "triplicate"))


class InterStateTests(TaxInvoiceTestCase):
    def test_integrated_tax_by_rate_and_where_it_was_delivered(self):
        site = self.address(self.ka, "Plot 7, KIADB", "Dharwad", "Karnataka", "580011",
                            kind=AddressType.SHIPPING)
        invoice = self.invoice(self.ka, [(self.sack, "1000", "125", "2", self.pair),
                                         (self.freight6, "1", "3000", "0", self.pair)], post=False)
        invoice.shipping_address = site
        invoice.save()
        invoice.post()
        printed = tax_invoice(invoice)
        self.assertEqual(printed["place_of_supply"], "Karnataka (29)")
        self.assertEqual(printed["recipient"]["state_name"], "Karnataka (29)")
        self.assertEqual(printed["taxes"], [("IGST", D("18"), D("22590.00"))])
        self.assertEqual([(line["hsn"], line["taxable"], line["taxes"]) for line in printed["lines"]], [
            ("63053300", D("122500.00"), [("IGST", D("18"), D("22050.00"))]),
            ("996511", D("3000.00"), [("IGST", D("18"), D("540.00"))])])
        self.assertEqual(printed["delivery"]["address"], ["Plot 7, KIADB", "Dharwad Karnataka 580011"])
        text = self.text(invoice)
        for expected in ["Karnataka (29)", "IGST 18% 22,050.00", "144,550.00", "IGST @ 18%", "22,590.00",
                         "148,090.00", "DELIVERED TO", "Plot 7, KIADB",
                         "less 2%"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        self.assertNotIn("CGST", text)


class ExportTests(TaxInvoiceTestCase):
    def test_under_letter_of_undertaking(self):
        invoice = self.export([(self.sack, "1000", "1.37", "0", self.pair)])
        printed = tax_invoice(invoice)
        self.assertEqual(printed["place_of_supply"], "Other country (96)")
        self.assertEqual(printed["taxes"], [("IGST", D("0"), D("0.00"))])
        self.assertEqual(printed["export"], "SUPPLY MEANT FOR EXPORT UNDER BOND OR LETTER OF UNDERTAKING "
                                            "WITHOUT PAYMENT OF INTEGRATED TAX")
        text = self.text(invoice)
        self.assertIn("SUPPLY MEANT FOR EXPORT UNDER BOND OR LETTER OF UNDERTAKING", text)
        self.assertIn("Unregistered", text)

    def test_on_payment_of_integrated_tax(self):
        from apps.core.models import Country

        abroad = self.party("EXPORT-P", currency=self.usd, gst_registration="overseas")
        self.address(abroad, "Jebel Ali Free Zone", "Dubai", "", "",
                     country=Country.objects.create(code="AE", name="United Arab Emirates"))
        invoice = self.invoice(abroad, [(self.sack, "1000", "1.37", "0", self.pair)], currency=self.usd)
        printed = tax_invoice(invoice)
        self.assertEqual(printed["taxes"], [("IGST", D("18"), D("246.60"))])
        self.assertEqual(invoice.total(), D("1616.60"))
        self.assertEqual(printed["export"], "SUPPLY MEANT FOR EXPORT ON PAYMENT OF INTEGRATED TAX")
        self.assertIn("SUPPLY MEANT FOR EXPORT ON PAYMENT OF INTEGRATED TAX", self.text(invoice))


class LetterOfUndertakingTests(TaxInvoiceTestCase):
    """O218 follow-up: the LUT an export is made under, kept as it stood and printed beside the words."""

    def export_on(self, day, post=True):
        from apps.core.models import Country

        abroad = self.party(f"EXP-{day:%m%d}", currency=self.usd, gst_registration="overseas",
                            fiscal_position=self.lut)
        self.address(abroad, "Jebel Ali Free Zone", "Dubai", "", "",
                     country=Country.objects.get_or_create(code="AE", name="United Arab Emirates")[0])
        return self.invoice(abroad, [(self.sack, "1000", "1.37", "0", self.pair)], currency=self.usd, day=day,
                            post=post)

    def test_the_year_changes_on_1_april(self):
        import datetime

        from apps.accounting.models import LetterOfUndertaking

        LetterOfUndertaking.objects.create(
            registration=self.settings, arn="AD2703250099999", financial_year="2025-26",
            filed_on=datetime.date(2025, 3, 25), valid_from=datetime.date(2025, 4, 1),
            valid_to=datetime.date(2026, 3, 31))
        march, april = self.export_on(datetime.date(2026, 3, 31)), self.export_on(datetime.date(2026, 4, 1))
        self.assertEqual((march.issued_as["lut"]["arn"], april.issued_as["lut"]["arn"]),
                         ("AD2703250099999", "AD2703260012345"))
        text = self.text(april)
        self.assertIn("WITHOUT PAYMENT OF INTEGRATED TAX LUT ARN AD2703260012345 dated 20 Mar 2026, for 2026-27", text)
        self.assertIn("LUT ARN AD2703250099999 dated 25 Mar 2025, for 2025-26", self.text(march))

    def test_refused_at_posting_with_no_lut_in_force(self):
        import datetime

        from django.core.exceptions import ValidationError

        draft = self.export_on(datetime.date(2026, 3, 31), post=False)
        with self.assertRaisesMessage(ValidationError, "is an export or SEZ supply without IGST dated 31 Mar 2026, "
                                                       "and no Letter of Undertaking is in force on that day"):
            draft.post()
        draft.refresh_from_db()
        self.assertFalse(draft.posted)

    def test_kept_as_it_stood(self):
        import datetime

        from django.core.exceptions import ValidationError

        invoice = self.export_on(datetime.date(2026, 9, 10))
        self.lut_2026.arn = "AD2703260054321"
        with self.assertRaisesMessage(ValidationError, "Posted invoices carry this LUT as it stood"):
            self.lut_2026.save()
        note = invoice.create_credit_note()
        self.assertEqual(note.issued_as["lut"]["arn"], "AD2703260012345")

    def test_one_financial_year_each(self):
        import datetime

        from django.core.exceptions import ValidationError

        from apps.accounting.models import LetterOfUndertaking

        with self.assertRaisesMessage(ValidationError, "is in 2026-27, not 2025-26"):
            LetterOfUndertaking.objects.create(
                registration=self.settings, arn="X1", financial_year="2025-26", filed_on=datetime.date(2026, 4, 2),
                valid_from=datetime.date(2026, 4, 2), valid_to=datetime.date(2027, 3, 31))


class CreditNoteTests(TaxInvoiceTestCase):
    def test_it_names_the_invoice_it_credits(self):
        invoice = self.p1()
        note = invoice.create_credit_note(quantities={invoice.lines.get(): D("10")})
        printed = tax_invoice(note)
        self.assertEqual(printed["heading"], "Credit Note")
        self.assertEqual(printed["credits"], {"number": invoice.number, "date": invoice.invoice_date})
        self.assertEqual(printed["taxes"], [("CGST", D("9"), D("135.00")), ("SGST", D("9"), D("135.00"))])
        self.assertEqual(printed["place_of_supply"], "Maharashtra (27)")
        text = self.text(note)
        for expected in ["Credit Note", f"{invoice.number} dated 10 Sep 2026", "63053300", "10.00 kg",
                         "1,500.00", "CGST @ 9%", "1,770.00"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, text)


class WhatWasIssuedTests(TaxInvoiceTestCase):
    def test_a_reprint_says_what_was_issued_not_what_stands_today(self):
        invoice = self.p1()
        before = tax_invoice(invoice)
        Company.objects.update(name="Deccan Sacks", legal_name="Deccan Sacks LLP")
        self.mh.legal_name = "Renamed Buyer"
        self.mh.save()
        self.mh.tax_profile.gstin = gstin("27AABCM9999A1Z")
        self.mh.tax_profile.save()
        GstSettings.objects.filter(pk=self.settings.pk).update(gstin=gstin("27AABCD7777E1Z"))
        self.sack.hsn_code = "39269099"
        self.sack.save()
        after = tax_invoice(invoice)
        self.assertEqual(after, before)
        text = self.text(invoice)
        self.assertIn("Deccan Polysacks Private Limited", text)
        self.assertIn(f"GSTIN {self.settings.gstin}", text)
        self.assertNotIn("Renamed Buyer", text)
        self.assertIn("63053300", text)


class ThroughTheApiTests(TaxInvoiceTestCase):
    def test_receivables_prints_each_copy_and_an_unknown_one_is_refused(self):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("receivables")
        user.groups.add(Group.objects.get(name="AR Manager"))
        client = APIClient()
        client.force_authenticate(user)
        invoice = self.p1()
        url = f"/api/sales/invoices/{invoice.pk}/pdf/"
        response = client.get(url, {"copy": "duplicate"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("-duplicate.pdf", response["Content-Disposition"])
        refused = client.get(url, {"copy": "quadruplicate"})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("is not a copy", str(refused.content))


class LinesLockedInKeyOrderTests(TaxInvoiceTestCase):
    """
    record_taxes writes each line's kept HSN and net, and each write holds the row. Read in no order, the
    lines came back as PostgreSQL keeps them, and a line updated earlier in the transaction is kept last:
    "sales.invoiceline 83 locked after sales.invoiceline 95". Shows on PostgreSQL; SQLite keeps rowid order.
    """

    def test_a_line_written_before_posting_is_still_locked_in_its_turn(self):
        from apps.sales.models import InvoiceLine

        lines = [(self.sack, "7", "33.33", "0", self.pair), (self.sack, "3", "0.35", "0", self.pair)]
        lines += [(self.sack, "1", "0.05", "0", self.pair)] * 11
        invoice = self.invoice(self.mh, lines, post=False)
        first = invoice.lines.order_by("pk").first()
        # Changed and changed back on an indexed column: no HOT update, so the row and its index entry
        # both move behind the other twelve, as a line edited before posting does.
        from apps.inventory.models import Item

        other = Item.objects.create(sku="SACK-B", name="Woven sack B", uom=self.sack.uom, hsn_code="63053300")
        InvoiceLine.objects.filter(pk=first.pk).update(item=other)
        InvoiceLine.objects.filter(pk=first.pk).update(item=self.sack, description="Woven sack, first line")
        invoice.post()
        self.assertEqual(invoice.lines.exclude(hsn_code="63053300").count(), 0)
