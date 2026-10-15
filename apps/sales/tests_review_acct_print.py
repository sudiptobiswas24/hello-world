"""
review_acct probes, g21 O218 (the Rule 46 invoice) and the LUT. Each test is a claim about
a document or about money; a failure is a finding. Not part of the product's suite.

  python manage.py test apps.sales.tests_review_acct_print
"""

import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.gst import GstSettings
from apps.accounting.models import JournalLine, LetterOfUndertaking, Tax
from apps.core.models import Address, AddressType, Company, Party, UnitOfMeasure
from apps.gst.tests import gstin
from apps.gst.tests_tax_invoice import printed_text
from apps.inventory.models import Item
from apps.sales.documents import render_invoice_pdf, tax_invoice
from apps.sales.tests_tcs import TcsTestCase


class Probe(TcsTestCase):
    def setUp(self):
        super().setUp()
        Company.objects.update(legal_name="Deccan Polysacks Private Limited", tax_id="TAXID-OLD")

    def text(self, invoice, copy="original"):
        return " ".join(" ".join(printed_text(render_invoice_pdf, invoice, copy)).split())

    def posted(self):
        site = self.address(self.mh, "Plot 9 Shipping Lane", "Nagpur", "Maharashtra", "440001",
                            kind=AddressType.SHIPPING)
        invoice = self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)], post=False)
        invoice.shipping_address = site
        invoice.save()
        invoice.post()
        return invoice

    def reprint_says(self, change, old, new):
        """Post, change something after posting, reprint: every `old` still printed, no `new` printed."""
        invoice = self.posted()
        before = self.text(invoice)
        for token in old:
            self.assertIn(token, before, f"fixture: {token!r} was not printed to begin with")
        change(invoice)
        invoice.refresh_from_db()
        after = self.text(invoice)
        for token in old:
            self.assertIn(token, after, f"{token!r} printed at posting is gone from the reprint")
        for token in new:
            self.assertNotIn(token, after, f"{token!r} did not exist when the invoice posted but the reprint prints it")


class ReprintReadsWhatWasIssued(Probe):
    def test_company_renamed(self):
        self.reprint_says(lambda i: Company.objects.update(name="Renamed Sacks", legal_name="Renamed Sacks LLP",
                                                           tax_id="TAXID-NEW"),
                          ["Deccan Polysacks Private Limited"], ["Renamed", "TAXID-NEW"])

    def test_company_tax_id_in_the_footer(self):
        self.reprint_says(lambda i: Company.objects.update(tax_id="TAXID-NEW"), ["Deccan Polysacks"], ["TAXID-NEW"])

    def test_company_moved(self):
        def move(invoice):
            Address.objects.filter(pk=Company.get().address_id).update(line1="Plot 99 New Estate")
        self.reprint_says(move, ["Plot 12, MIDC Chakan"], ["Plot 99 New Estate"])

    def test_customer_renamed(self):
        self.reprint_says(lambda i: Party.objects.filter(pk=self.mh.pk).update(name="Renamed Buyer", legal_name="Renamed Buyer Ltd"),
                          ["MH-REG"], ["Renamed Buyer"])

    def test_billing_address_edited(self):
        self.reprint_says(lambda i: Address.objects.filter(party=self.mh, address_type=AddressType.BILLING).update(line1="NEW BILLING LINE"),
                          ["3 Market Yard"], ["NEW BILLING LINE"])

    def test_delivery_address_edited(self):
        self.reprint_says(lambda i: Address.objects.filter(party=self.mh, address_type=AddressType.SHIPPING).update(line1="NEW SHIPPING LINE"),
                          ["Plot 9 Shipping Lane"], ["NEW SHIPPING LINE"])

    def test_customer_gstin_changed(self):
        new = gstin("27AABCX9999Z1Z")
        old = self.mh.tax_profile.gstin
        self.reprint_says(lambda i: type(self.mh.tax_profile).objects.filter(pk=self.mh.tax_profile.pk).update(gstin=new),
                          [f"GSTIN {old}"], [new])

    def test_company_gstin_changed(self):
        new = gstin("27AABCZ7777Z1Z")
        old = self.settings.gstin
        self.reprint_says(lambda i: GstSettings.objects.filter(pk=self.settings.pk).update(gstin=new),
                          [f"GSTIN {old}"], [new])

    def test_hsn_changed(self):
        self.reprint_says(lambda i: Item.objects.filter(pk=self.sack.pk).update(hsn_code="99999999"),
                          ["63053300"], ["99999999"])

    def test_item_renamed(self):
        self.reprint_says(lambda i: Item.objects.filter(pk=self.sack.pk).update(name="RENAMED ITEM NAME"),
                          ["Woven sack"], ["RENAMED ITEM NAME"])

    def test_unit_renamed(self):
        self.reprint_says(lambda i: UnitOfMeasure.objects.filter(pk=self.sack.uom_id).update(code="KGM"),
                          ["100.00 kg"], ["100.00 KGM"])

    def test_rate_changed(self):
        self.reprint_says(lambda i: Tax.objects.filter(code__in=["CGST9", "SGST9"]).update(rate=D("6")),
                          ["CGST 9%", "SGST 9%", "1,350.00"], ["CGST 6%", "SGST 6%"])

    def test_the_tax_invoice_dict_is_the_same_after_every_change_at_once(self):
        invoice = self.posted()
        before = tax_invoice(invoice)
        Company.objects.update(name="X", legal_name="X Ltd")
        Party.objects.filter(pk=self.mh.pk).update(name="Y", legal_name="Y Ltd")
        Item.objects.filter(pk=self.sack.pk).update(name="Z", hsn_code="11111111")
        Tax.objects.filter(code__in=["CGST9", "SGST9"]).update(rate=D("6"))
        invoice.refresh_from_db()
        after = tax_invoice(invoice)
        for key in before:
            with self.subTest(field=key):
                self.assertEqual(before[key], after[key])


class FootingToTheEntry(Probe):
    def foot(self, invoice):
        printed = tax_invoice(invoice)
        by_head = {name: sum((a for n, _, a in printed["taxes"] if n == name), D("0")) for name in ("CGST", "SGST", "IGST")}
        credit = lambda code: (JournalLine.objects.filter(account__code=code, entry__posted=True, entry__reference=invoice.number)
                               .aggregate(c=Sum("credit"), d=Sum("debit")))
        posted_cgst = (credit("2201")["c"] or D("0")) - (credit("2201")["d"] or D("0"))
        posted_sgst = (credit("2202")["c"] or D("0")) - (credit("2202")["d"] or D("0"))
        posted_igst = (credit("2203")["c"] or D("0")) - (credit("2203")["d"] or D("0"))
        self.assertEqual((by_head["CGST"], by_head["SGST"], by_head["IGST"]), (posted_cgst, posted_sgst, posted_igst),
                         "printed tax by head differs from what the entry posted")
        per_line = sum((line["amount"] for line in printed["lines"]), D("0"))
        self.assertEqual(per_line, invoice.total() - invoice.tcs_amount,
                         "the printed lines do not add up to the printed total")
        self.assertEqual(printed["taxable"] + sum((a for _, _, a in printed["taxes"]), D("0")),
                         invoice.total() - invoice.tcs_amount, "taxable plus the tax heads is not the total")
        ar = (JournalLine.objects.filter(account__code="1100", entry__posted=True, entry__reference=invoice.number)
              .aggregate(d=Sum("debit"), c=Sum("credit")))
        self.assertEqual((ar["d"] or D("0")) - (ar["c"] or D("0")), invoice.total(), "the receivable is not the total")

    def test_many_small_lines_intra_state(self):
        lines = [(self.sack, "7", "33.33", "0", self.pair), (self.sack, "3", "0.35", "0", self.pair)]
        lines += [(self.sack, "1", "0.05", "0", self.pair)] * 11
        self.foot(self.invoice(self.mh, lines))

    def test_many_small_lines_with_a_discount(self):
        lines = [(self.sack, "13", "7.77", "3.5", self.pair)] * 5 + [(self.sack, "1", "0.15", "12.5", self.pair)] * 7
        self.foot(self.invoice(self.mh, lines))

    def test_inter_state_with_discount_and_freight(self):
        self.foot(self.s1())

    def test_scrap_with_tcs(self):
        self.foot(self.invoice(self.mh, [(self.scrap, "333", "17.17", "0", self.pair), (self.sack, "9", "1.11", "0", self.pair)]))


class TheLetterOfUndertaking(Probe):
    def export_on(self, day):
        return self.export_dated(day)

    def export_dated(self, day):
        from apps.accounting.models import FiscalPosition  # noqa: F401
        from apps.core.models import Country

        self.n = getattr(self, "n", 0) + 1
        abroad = self.party(f"EXPORT2-{self.n}", currency=self.usd, gst_registration="overseas", fiscal_position=self.lut)
        uae = Country.objects.get_or_create(code="AE", defaults={"name": "United Arab Emirates"})[0]
        self.address(abroad, "Jebel Ali Free Zone", "Dubai", "", "", country=uae)
        return self.invoice(abroad, [(self.sack, "1000", "1.37", "0", self.pair)], currency=self.usd, day=day)

    def test_an_lut_filed_after_the_invoice_does_not_cover_it(self):
        """Rule 96A: the LUT is furnished before the export. 2 April invoice, LUT filed on 5 April."""
        LetterOfUndertaking.objects.create(
            registration=self.settings, arn="AD2704270099999", financial_year="2027-28",
            filed_on=datetime.date(2027, 4, 5), valid_from=datetime.date(2027, 4, 1), valid_to=datetime.date(2028, 3, 31))
        with self.assertRaises(ValidationError):
            self.export_dated(datetime.date(2027, 4, 2))

    def test_a_posted_invoice_keeps_its_lut_when_the_lut_is_edited_every_way(self):
        invoice = self.export_dated(datetime.date(2026, 9, 10))
        self.assertEqual(invoice.issued_as["lut"]["arn"], "AD2703260012345")
        for field, value in [("arn", "CHANGED000000001"), ("filed_on", datetime.date(2026, 3, 21)),
                             ("valid_from", datetime.date(2026, 4, 2)), ("valid_to", datetime.date(2027, 3, 30)),
                             ("financial_year", "2026-27")]:
            lut = LetterOfUndertaking.objects.get(pk=self.lut_2026.pk)
            setattr(lut, field, value)
            if field == "financial_year":
                continue
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    lut.save()
        with self.assertRaises(ValidationError):
            LetterOfUndertaking.objects.get(pk=self.lut_2026.pk).delete()

    def test_a_second_lut_for_the_same_days_does_not_change_what_an_invoice_carries(self):
        invoice = self.export_dated(datetime.date(2026, 9, 10))
        LetterOfUndertaking.objects.create(
            registration=self.settings, arn="AD2709260000001", financial_year="2026-27",
            filed_on=datetime.date(2026, 9, 1), valid_from=datetime.date(2026, 9, 1), valid_to=datetime.date(2027, 3, 31))
        invoice.refresh_from_db()
        self.assertIn("AD2703260012345", self.text(invoice))

    def test_a_credit_note_in_the_next_financial_year_keeps_the_invoices_lut(self):
        LetterOfUndertaking.objects.create(
            registration=self.settings, arn="AD2703250011111", financial_year="2025-26",
            filed_on=datetime.date(2025, 3, 20), valid_from=datetime.date(2025, 4, 1), valid_to=datetime.date(2026, 3, 31))
        invoice = self.export_dated(datetime.date(2026, 3, 20))
        note = invoice.create_credit_note(on_date=datetime.date(2026, 4, 2))
        self.assertEqual(note.issued_as["lut"]["arn"], "AD2703250011111")
        self.assertIn("AD2703250011111", self.text(note))

    def test_31_march_and_1_april(self):
        self.export_dated(datetime.date(2027, 3, 31))
        with self.assertRaises(ValidationError):
            self.export_dated(datetime.date(2027, 4, 1))

    def test_an_sez_supply_without_igst_prints_sez_words_and_the_lut(self):
        sez = self.party("SEZ1", gst_registration="sez", fiscal_position=self.lut)
        self.address(sez, "SEZ Phase 2", "Pune", "Maharashtra", "411057")
        invoice = self.invoice(sez, [(self.sack, "100", "150", "0", self.pair)])
        text = self.text(invoice)
        self.assertIn("SEZ UNIT OR SEZ DEVELOPER", text)
        self.assertIn("AD2703260012345", text)

    def test_an_sez_supply_with_igst(self):
        sez = self.party("SEZ2", gst_registration="sez", gstin=gstin("27AABCS3333S1Z"))
        self.address(sez, "SEZ Phase 3", "Pune", "Maharashtra", "411057")
        invoice = self.invoice(sez, [(self.sack, "100", "150", "0", self.pair)])
        printed = tax_invoice(invoice)
        self.assertIn("ON PAYMENT OF INTEGRATED TAX", printed["export"] or "",
                      f"taxes charged on an SEZ supply (registered, same-state GSTIN, no fiscal position): {printed['taxes']}")

    def test_an_export_with_igst_prints_igst_and_no_lut_words(self):
        from apps.core.models import Country

        abroad = self.party("EXPORT3", currency=self.usd, gst_registration="overseas", fiscal_position=None)
        uae = Country.objects.get_or_create(code="AE", defaults={"name": "United Arab Emirates"})[0]
        self.address(abroad, "Jebel Ali", "Dubai", "", "", country=uae)
        invoice = self.invoice(abroad, [(self.sack, "1000", "1.37", "0", self.igst and [self.igst])], currency=self.usd)
        printed = tax_invoice(invoice)
        self.assertIn("ON PAYMENT OF INTEGRATED TAX", printed["export"])
        self.assertNotIn("WITHOUT PAYMENT", self.text(invoice))


    def test_a_taxed_sez_supply_is_not_refused_for_want_of_an_lut(self):
        """CGST and SGST were charged: the invoice is not made under bond or LUT, so no LUT is wanted."""
        LetterOfUndertaking.objects.all().delete()
        sez = self.party("SEZ3", gst_registration="sez", gstin=gstin("27AABCS4444S1Z"))
        self.address(sez, "SEZ Phase 4", "Pune", "Maharashtra", "411057")
        invoice = self.invoice(sez, [(self.sack, "100", "150", "0", self.pair)])
        self.assertTrue(invoice.posted)

    def test_the_words_on_an_invoice_that_charged_cgst_and_sgst_do_not_say_without_payment_of_tax(self):
        sez = self.party("SEZ4", gst_registration="sez", gstin=gstin("27AABCS5555S1Z"))
        self.address(sez, "SEZ Phase 5", "Pune", "Maharashtra", "411057")
        invoice = self.invoice(sez, [(self.sack, "100", "150", "0", self.pair)])
        printed = tax_invoice(invoice)
        self.assertEqual([name for name, _, _ in printed["taxes"]], ["CGST", "SGST"])
        self.assertNotIn("WITHOUT PAYMENT", printed["export"] or "")
