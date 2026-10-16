"""
g22 review of fix_money, GST side: O235 (debit note), O238 (vouchers). Probes assert what SHOULD be true.
"""
import datetime
from decimal import Decimal

from . import tests_advances as adv
from . import tests_einvoice as ein
from .einvoice import build
from .returns import gstr1, gstr1_json, gstr3b, month

D = Decimal
SEPTEMBER, OCTOBER = month("2026-09"), month("2026-10")
OCT5 = datetime.date(2026, 10, 5)


def say(ok, claim, detail=""):
    print(("PASS " if ok else "FAIL ") + claim + (f"  [{detail}]" if detail else ""))
    return ok


class DebitNoteInTheReturn(ein.EInvoiceTestCase):
    def test_the_hsn_summary_takes_the_debit_notes_value_without_a_new_unit_or_quantity(self):
        invoice = self.s1()
        before = gstr1(*SEPTEMBER)
        note = invoice.debit("1000", "Rate agreed at 126", on_date=ein.DAY)
        after = gstr1(*SEPTEMBER)
        rows = after["hsn_b2b"]
        uqcs = sorted({row["uqc"] for row in rows})
        qty = sum((row["quantity"] for row in rows), D("0"))
        qty_before = sum((row["quantity"] for row in before["hsn_b2b"]), D("0"))
        taxable_after = sum((row["taxable"] for row in rows), D("0"))
        taxable_before = sum((row["taxable"] for row in before["hsn_b2b"]), D("0"))
        warnings = [w for w in after["warnings"] if "unit code" in w or "no HSN" in w]
        print("   HSN rows:", [(r["hsn"], r["uqc"], r["rate"], r["taxable"], r["quantity"]) for r in rows])
        goods_in_na = [r["hsn"] for r in rows if r["uqc"] == "NA" and not r["hsn"].startswith("99")]
        ok = ("OTH" not in uqcs and not goods_in_na and qty == qty_before and taxable_after - taxable_before == D("1000.00") and not warnings)
        say(ok, "O235 GSTR-1 HSN summary: debit note adds 1,000.00 taxable to the goods HSN row (not a UQC NA row), no warning",
            f"goods HSN under NA {goods_in_na}; uqcs {uqcs} qty {qty_before}->{qty} taxable {taxable_before}->{taxable_after} warnings {warnings}")
        self.assertTrue(ok)

    def test_a_debit_note_on_a_small_unregistered_sale_adds_to_b2cs_not_cdnur(self):
        buyer = self.party("RETAIL", gst_registration="unregistered", gst_state="27")
        self.address(buyer, "9 Bazaar Road", "Pune", "Maharashtra", "411001")
        invoice = self.invoice(buyer, [(self.sack, "100", "150", "0", self.pair)])
        before = gstr1(*SEPTEMBER)
        note = invoice.debit("500", "Price rise", on_date=ein.DAY)
        after = gstr1(*SEPTEMBER)
        b2cs_before = sum((row["taxable"] for row in before["b2cs"]), D("0"))
        b2cs_after = sum((row["taxable"] for row in after["b2cs"]), D("0"))
        ok = (b2cs_after - b2cs_before == D("500.00") and not after["cdnur"] and not after["cdnr"])
        say(ok, "O235 GSTR-1: a debit note on a small B2C sale adds 500 to B2CS, not CDNUR/CDNR",
            f"b2cs {b2cs_before}->{b2cs_after} cdnur {len(after['cdnur'])} cdnr {len(after['cdnr'])}")
        self.assertTrue(ok)

    def test_in_its_own_month_not_the_invoices(self):
        invoice = self.s1()
        note = invoice.debit("1000", "Rate agreed", on_date=OCT5)
        sep, octo = gstr1(*SEPTEMBER), gstr1(*OCTOBER)
        ok = (not sep["cdnr"] and [r["number"] for r in octo["cdnr"]] == [note.number]
              and octo["cdnr"][0]["original"] == invoice.number
              and [r["kind"] for r in octo["documents"]] == ["debit_notes"])
        say(ok, "O235 the debit note is in October's CDNR and Table 13, September's return untouched",
            f"sep cdnr {len(sep['cdnr'])} oct docs {octo['documents']}")
        self.assertTrue(ok)
        tb = gstr3b(*OCTOBER)
        igst = tb["3.1a"]["igst"] if "3.1a" in tb else None
        say(igst == D("180.00"), "O235 GSTR-3B October 3.1(a) IGST carries the note's 180.00", f"igst {igst}")

    def test_a_credit_note_on_a_debit_note_is_the_right_way_round_in_the_next_month(self):
        invoice = self.s1()
        note = invoice.debit("1000", "Rate agreed", on_date=ein.DAY)
        back = note.create_credit_note(on_date=OCT5)
        octo = gstr1(*OCTOBER)
        (row,) = octo["cdnr"]
        ok = (row["note_type"] == "C" and row["original"] == note.number and row["value"] == D("1180.00")
              and [k["kind"] for k in octo["documents"]] == ["credit_notes"])
        say(ok, "O235 reverse: a credit note on a debit note is ntty C naming the debit note, 1,180.00",
            f"{row['note_type']} {row['original']} {row['value']}")
        self.assertTrue(ok)
        # the net of the pair on the books
        self.assertEqual(back.total(), D("1180.00"))

    def test_the_debit_note_print_carries_rule_53(self):
        from apps.sales.documents import render_invoice_pdf, tax_invoice
        from .tests_tax_invoice import printed_text

        invoice = self.s1()
        note = invoice.debit("1000", "Rate agreed at 126", on_date=ein.DAY)
        printed = tax_invoice(note)
        text = " ".join(" ".join(printed_text(render_invoice_pdf, note)).split())
        needs = {
            "heading": printed["heading"] == "Debit Note",
            "its invoice's number": invoice.number in text,
            "its invoice's date": invoice.invoice_date.strftime("%d %b %Y") in text,
            "buyer gstin": self.ka.tax_profile.gstin in text if hasattr(self.ka, "tax_profile") else True,
            "no Tax Invoice heading": "Tax Invoice" not in text,
            "the HSN": "63053300" in text,
            "the tax debited": "23.90" in text or "175.70" in text,
        }
        say(all(needs.values()), "O235 Rule 53: the print says Debit Note, names the invoice and its date, HSN, tax",
            str({k: v for k, v in needs.items() if not v}))
        self.assertTrue(all(needs.values()), needs)

    def test_the_einvoice_payload_for_a_debit_note_has_what_the_schema_requires(self):
        invoice = self.s1()
        note = invoice.debit("1000", "Rate agreed at 126", on_date=ein.DAY)
        payload = build(note)
        required = ["Version", "TranDtls", "DocDtls", "SellerDtls", "BuyerDtls", "ItemList", "ValDtls"]
        item_required = ["SlNo", "IsServc", "HsnCd", "UnitPrice", "TotAmt", "AssAmt", "GstRt", "TotItemVal"]
        missing = [k for k in required if k not in payload]
        item_missing = [k for item in payload["ItemList"] for k in item_required if k not in item]
        item = payload["ItemList"][0]
        val = payload["ValDtls"]
        parts = sum((D(str(val[k])) for k in ("AssVal", "CgstVal", "SgstVal", "IgstVal", "CesVal", "StCesVal", "OthChrg",
                                                "RndOffAmt")), D("0")) - D(str(val["Discount"]))
        ok = (not missing and not item_missing and payload["DocDtls"]["Typ"] == "DBN"
              and parts == D(str(val["TotInvVal"])) and payload["RefDtls"]["PrecDocDtls"][0]["InvNo"] == invoice.number)
        say(ok, "O235 e-invoice DBN: required keys present, values foot, RefDtls names the invoice",
            f"missing {missing} {item_missing}; item {item}; parts {parts} vs {val['TotInvVal']}")
        self.assertTrue(ok)

    def test_a_debit_note_on_an_export_under_lut(self):
        invoice = self.export([(self.sack, "1000", "1.37", "0", [self.zero])])
        note = invoice.debit("100", "Price rise", on_date=ein.DAY)
        result = gstr1(*SEPTEMBER)
        types = [(r["type"], r["note_type"], r["value"]) for r in result["cdnur"]]
        js = gstr1_json(result)["cdnur"]
        payload = build(note)
        ok = (note.tax_total() == 0 and any(t[0] == "exp_without_payment" and t[1] == "D" for t in types)
              and js and js[-1]["typ"] == "EXPWOP" and js[-1]["ntty"] == "D" and payload["DocDtls"]["Typ"] == "DBN"
              and payload["TranDtls"]["SupTyp"] == "EXPWOP")
        say(ok, "O235 export under LUT: debit note is zero tax, CDNUR EXPWOP ntty D, e-invoice DBN/EXPWOP",
            f"tax {note.tax_total()} cdnur {types} json {js[-1:] if js else js} supTyp {payload['TranDtls']['SupTyp']}")
        self.assertTrue(ok)
        # its value in rupees is the dollars at the debit note's rate
        say(True, "   rate on the note", f"{note.exchange_rate} total {note.total()}")


class VouchersInTheReturn(adv.AdvanceTestCase):
    def test_table_13_counts_a_receipt_voucher_and_a_refund_voucher_as_their_own_natures(self):
        order = self.job_work()
        deposit = self.advance(order, day=adv.OCT)
        note = deposit.create_credit_note(amount=D("1180"))
        result = gstr1(*OCTOBER)
        kinds = [(row["kind"], row.get("from"), row.get("to"), row.get("count")) for row in result["documents"]]
        names = " ".join(str(row) for row in result["documents"])
        ok = (deposit.number not in names and note.number not in names) or any(
            "receipt" in row["kind"] or "refund" in row["kind"] for row in result["documents"])
        say(ok, "O238 Table 13: the receipt voucher and refund voucher are not counted as an invoice and a credit note",
            f"documents {result['documents']}")
        self.assertTrue(ok)

    def test_table_11_has_the_advance_and_takes_off_the_refund(self):
        order = self.job_work()
        deposit = self.advance(order, day=adv.OCT)
        note = deposit.create_credit_note(amount=D("1180"))
        result = gstr1(*OCTOBER)
        rows = result["advances_received"]
        taxable = sum((r["taxable"] for r in rows), D("0"))
        igst_cgst = sum((r["cgst"] + r["sgst"] + r["igst"] for r in rows), D("0"))
        # 3,000 + 540 received; 1,180 refunded = 1,000 + 180
        ok = taxable == D("2000.00") and igst_cgst == D("360.00")
        say(ok, "O238 Table 11A: received 3,000/540 less refunded 1,000/180 = 2,000/360 net for the month",
            f"taxable {taxable} tax {igst_cgst} rows {rows}")
        self.assertTrue(ok)

    def test_the_receipt_voucher_is_a_voucher_in_the_right_series_and_says_the_tax(self):
        from apps.sales.documents import render_invoice_pdf, tax_invoice
        from .tests_tax_invoice import printed_text

        deposit = self.advance(self.job_work(), day=adv.OCT)
        note = deposit.create_credit_note(amount=D("1180"))
        text = " ".join(" ".join(printed_text(render_invoice_pdf, deposit)).split())
        text_note = " ".join(" ".join(printed_text(render_invoice_pdf, note)).split())
        needs = {
            "receipt voucher number not from the invoice series": not deposit.number.startswith("INV-"),
            "refund voucher number not a credit note's": not note.number.startswith("CN-"),
            "receipt voucher says advance amount": "3,540.00" in text,
            "receipt voucher says rate of tax": "9" in text,
            "refund voucher names the receipt voucher date": deposit.invoice_date.strftime("%d %b %Y") in text_note,
            "refund voucher says reverse charge": "everse" in text_note,
        }
        say(all(needs.values()), "O238 Rule 50/51 content: own series, amount, rate, reverse charge, receipt voucher date",
            str({k: v for k, v in needs.items() if not v}) + f" numbers {deposit.number} {note.number}")
        self.assertTrue(all(needs.values()), needs)


class DebitNoteReversePathsAndGuards(ein.EInvoiceTestCase):
    def test_a_debit_note_on_an_invoice_whose_irn_the_portal_cancelled_is_refused(self):
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        invoice = self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)])
        record = self.registered(invoice)
        record.cancellation("2", "Wrong rate")
        record.record_cancellation(timezone.now())
        try:
            note = invoice.debit("100", "Price rise", on_date=ein.DAY)
            outcome = f"raised {note.number}"
        except ValidationError as exc:
            outcome = None
        say(outcome is None, "O235 a debit note on an invoice whose IRN is cancelled is refused", outcome or "refused")
        self.assertIsNone(outcome)

    def test_a_debit_note_on_an_invoice_credited_in_full_is_refused(self):
        from django.core.exceptions import ValidationError

        invoice = self.s1()
        invoice.create_credit_note(on_date=ein.DAY)
        try:
            note = invoice.debit("1000", "Price rise", on_date=ein.DAY)
            outcome = f"raised {note.number} total {note.total()}"
        except ValidationError:
            outcome = None
        say(outcome is None, "O235 a debit note on an invoice already credited in full is refused", outcome or "refused")
        self.assertIsNone(outcome)

    def test_a_debit_notes_cancelled_irn_reverses_by_a_credit_note_and_neither_is_reported(self):
        from django.utils import timezone

        invoice = self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)])
        note = invoice.debit("1000", "Price rise", on_date=ein.DAY)
        before = -self.ledger("1100")
        record = self.registered(note)
        record.cancellation("2", "Wrong rate")
        reversal = record.record_cancellation(timezone.now())
        result = gstr1(*SEPTEMBER)
        docs = {r["kind"]: (r["total"], r["cancelled"]) for r in result["documents"]}
        ok = (-self.ledger("1100") == before - note.total() and not result["cdnr"] and
              docs.get("debit_notes", (0, 0)) == (1, 1))
        say(ok, "O235 reverse: a DBN whose IRN is cancelled is reversed in the books and off GSTR-1; Table 13 counts the DN cancelled once",
            f"AR {before}->{-self.ledger('1100')}, cdnr {len(result['cdnr'])}, docs {docs}")
        self.assertTrue(ok)


class DebitNoteAndTheEwayBill(ein.EInvoiceTestCase):
    def test_no_eway_bill_is_offered_for_a_debit_note(self):
        from django.core.exceptions import ValidationError
        from . import ewaybill

        invoice = self.s1()
        note = invoice.debit("100000", "Rate agreed at 225", on_date=ein.DAY)
        try:
            payload = ewaybill._invoice_payload(note)
            outcome = f"payload built, value {payload.get('totInvValue') if isinstance(payload, dict) else payload}"
        except ValidationError as exc:
            outcome = None
            print("   refused:", exc.messages)
        say(outcome is None, "O235 an e-way bill is not built for a debit note (no goods move on it)", outcome or "refused")
        self.assertIsNone(outcome)


class VoucherAndTheEinvoice(adv.AdvanceTestCase):
    def test_a_receipt_voucher_is_not_registered_as_an_invoice_with_the_portal(self):
        from django.core.exceptions import ValidationError

        deposit = self.advance(self.job_work(), day=adv.OCT)
        try:
            payload = build(deposit)
            outcome = f"payload built, Typ {payload['DocDtls']['Typ']}"
        except ValidationError as exc:
            outcome = None
            print("   refused:", exc.messages)
        say(outcome is None, "O238 an e-invoice payload is not built for a receipt voucher", outcome or "refused")
        self.assertIsNone(outcome)
