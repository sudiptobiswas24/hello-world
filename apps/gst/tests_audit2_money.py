"""Second audit, lane money: probes that assert what SHOULD be true. Figures: calc_audit2_money/figures.py."""

import datetime

from apps.sales.models import Invoice, InvoiceLine

from .returns import gstr1, month
from .tests import DAY, D, GstReturnTestCase, gstin
from .tests_einvoice import EInvoiceTestCase

OCT = month("2026-10")


class Table13Tests(GstReturnTestCase):
    def test_table_13_leaves_out_the_old_systems_invoices_and_their_plain_credits(self):
        """M1: an opening-balance invoice takes an INV- number but is reported nowhere; table 13 counts it as issued."""
        buyer = self.party("B2B", gstin=gstin("27AAACB1234K1Z"), gst_state="27", gst_registration="regular")
        old = Invoice.objects.create(customer=buyer, invoice_date=datetime.date(2026, 10, 2), reference="OLD/1",
                                     receivable_account=self.ar, currency=self.inr, is_opening_balance=True)
        InvoiceLine.objects.create(invoice=old, description="Opening balance: OLD/1", quantity=D("1"),
                                   unit_price=D("1000"), revenue_account=self.revenue)
        old.post()
        self.sell(buyer, "1000", day=datetime.date(2026, 10, 3))
        self.sell(buyer, "1000", day=datetime.date(2026, 10, 4))
        old.create_credit_note(memo="Settled in the old system")
        Invoice.objects.filter(credits=old).update(invoice_date=datetime.date(2026, 10, 5))
        rows = gstr1(*OCT)["documents"]
        invoices = [row for row in rows if row["kind"] == "invoices"]
        credits = [row for row in rows if row["kind"] == "credit_notes"]
        self.assertEqual(sum(row["total"] for row in invoices), 2)
        self.assertEqual(sum(row["total"] for row in credits), 0)


class Gstr2bAvailabilityTests(GstReturnTestCase):
    def test_credit_the_portal_marks_not_available_is_not_counted_as_matched(self):
        """M5: a bill matched to a 2B line flagged ITC not available (itcavl N) still adds to totals.matched."""
        from apps.core.models import PartyRole

        from .gstr2b import keep, reconcile
        from .tests_gstr2b import KA, inv, portal_json

        ka = self.party("KA", role=PartyRole.VENDOR, gstin=KA, gst_state="29")
        self.buy(ka, "10000", taxes=[self.igst], reference="INV/27/0012")
        blocked = inv("INV-27-12", "10-09-2026", "10000", igst="1800", itcavl="N", rsn="Supplier has not filed GSTR-3B")
        keep("2026-09", portal_json(b2b=[{"ctin": KA, "trdnm": "KA Mills", "inv": [blocked]}]))
        report = reconcile("2026-09")
        # 1,800 of IGST is booked; the portal says it is not available to the company this month: 0 matched.
        self.assertEqual(report["totals"]["matched"], D("0"))


class SezNoteTests(GstReturnTestCase):
    def test_a_credit_note_to_an_sez_unit_is_filed_as_an_sez_note_not_a_regular_one(self):
        """M6: gstr1_json writes every CDNR note as inv_typ R; the original invoice here is SEWOP (SEZ, no payment)."""
        from .returns import gstr1_json

        sez = self.party("SEZ", gstin=gstin("24AABCS3333C1Z"), gst_registration="sez", fiscal_position=self.lut)
        invoice = self.sell(sez, "5000")
        note = invoice.create_credit_note()
        Invoice.objects.filter(pk=note.pk).update(invoice_date=DAY)
        result = gstr1(*month("2026-09"))
        self.assertEqual([row["type"] for row in result["b2b"]], ["sez_without_payment"])
        [group] = gstr1_json(result)["cdnr"]
        self.assertEqual([row["inv_typ"] for row in group["nt"]], ["SEWOP"])


class PortalAnswerFromTomorrowTests(EInvoiceTestCase):
    """The portal answers with when it acted; an answer from a day to come is no answer (record_cancellation asks it)."""

    def setUp(self):
        super().setUp()
        from django.utils import timezone

        self.later = timezone.now() + datetime.timedelta(days=5)
        self.buyer = self.ka

    def test_an_e_way_bill_generated_next_week_is_refused(self):
        """M7: EwayBill.record checks the document's date but not that the generation time has come."""
        from django.core.exceptions import ValidationError

        from .ewaybill import prepare

        invoice = self.sell(self.buyer, "100000")
        bill = prepare(invoice, vehicle_number="MH12AB1234")
        with self.assertRaises(ValidationError):
            bill.record("123456789012", self.later, None)

    def test_an_irn_acknowledged_next_week_is_refused(self):
        """M8: EInvoice.record checks the ack is not before the invoice, not that it has come; the IRN then cannot be cancelled."""
        from django.core.exceptions import ValidationError

        from .einvoice import prepare
        from .tests_einvoice import IRN, signed_qr

        invoice = self.sell(self.buyer, "1000")
        record = prepare(invoice)
        with self.assertRaises(ValidationError):
            record.record(IRN, "112610000000001", self.later, signed_qr(
                Irn=IRN, DocNo=invoice.number, SellerGstin=self.settings.gstin,
                BuyerGstin=record.payload["BuyerDtls"]["Gstin"], TotInvVal=record.payload["ValDtls"]["TotInvVal"]))


class ReturnStaysTheSameTests(GstReturnTestCase):
    def test_a_month_recompiled_after_the_b2cl_limit_changes_reports_the_same_invoices(self):
        """M10: the module promises a return compiled twice gives the same figures; the B2CL limit is read live."""
        retail = self.party("KA-UNREG", gst_state="29", gst_registration="unregistered")
        self.sell(retail, "150000", taxes=[self.igst])
        before = gstr1(*month("2026-09"))
        self.assertEqual(len(before["b2cl"]), 1)
        self.settings.b2cl_limit = D("250000")
        self.settings.save()
        after = gstr1(*month("2026-09"))
        self.assertEqual((len(after["b2cl"]), after["b2cs"]), (len(before["b2cl"]), before["b2cs"]))


class RoundingAgreesEverywhereTests(GstReturnTestCase):
    def test_the_invoice_the_ledger_and_the_return_say_the_same_figures(self):
        """M12: forty odd-priced invoices (fractional kg, discounts, 5/18 and half-split taxes): document total,
        receivable debit, and GSTR-1 (items + tax) must agree to the paisa."""
        import random

        from django.db.models import Sum

        from apps.accounting.models import JournalLine

        rng = random.Random(23)
        buyer = self.party("MH", gstin=gstin("27AAACB1234K1Z"), gst_state="27", gst_registration="regular")
        five = self.igst.__class__.objects.create(code="CG25", name="CG25", rate=D("2.5"), gst_head="cgst",
                                                  collected_account=self.cgst.collected_account,
                                                  paid_account=self.cgst.paid_account)
        sg25 = self.igst.__class__.objects.create(code="SG25", name="SG25", rate=D("2.5"), gst_head="sgst",
                                                  collected_account=self.sgst.collected_account,
                                                  paid_account=self.sgst.paid_account)
        made = []
        for _ in range(40):
            invoice = Invoice.objects.create(customer=buyer, invoice_date=DAY, receivable_account=self.ar,
                                             currency=self.inr)
            for _ in range(rng.randint(1, 4)):
                line = InvoiceLine.objects.create(
                    invoice=invoice, item=self.sack, quantity=D(rng.randint(1, 9999)) / D(rng.choice([1, 10, 100, 1000])),
                    unit_price=D(rng.randint(100, 99999)) / D(rng.choice([100, 1000, 10000])),
                    discount_percent=D(rng.choice([0, 0, 2, 3.5, 7.25])), revenue_account=self.revenue)
                line.taxes.set(rng.choice([self.pair, [five, sg25]]))
            invoice.post()
            made.append(invoice)
        result = gstr1(*month("2026-09"))
        by_number = {row["number"]: row for row in result["b2b"]}
        bad = []
        for invoice in made:
            debit = JournalLine.objects.filter(entry=invoice.journal_entry, account=self.ar).aggregate(
                total=Sum("debit"))["total"]
            row = by_number[invoice.number]
            reported = sum((item["taxable"] + item["igst"] + item["cgst"] + item["sgst"] + item["cess"]
                            for item in row["items"]), D("0"))
            if not (invoice.total() == debit == row["value"] == reported):
                bad.append((invoice.number, invoice.total(), debit, row["value"], reported))
        self.assertEqual(bad, [])


class SwitchedOffTaxTests(GstReturnTestCase):
    def test_an_invoice_is_not_taxed_at_a_tax_switched_off(self):
        """M16: Tax.is_active is read by no document."""
        from django.core.exceptions import ValidationError

        buyer = self.party("MH", gstin=gstin("27AAACB1234K1Z"), gst_state="27", gst_registration="regular")
        self.cgst.is_active = False
        self.cgst.save()
        with self.assertRaises(ValidationError):
            self.sell(buyer, "1000")
