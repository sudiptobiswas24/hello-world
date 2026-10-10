"""
Credit against the 2B. September's bills: KA's INV/27/0012 (10,000 at
18% IGST), MH's MH-7 (10,000 at 9% + 9%), D's D-5 (2,000 at 9% + 9%) and
W's W-1 (5,000 at 18%). The portal's 2B carries KA's as INV-27-12 and
MH's as filed, D's at 2,500 (the supplier's figure differs), a second KA
invoice and a KA credit note that no bill answers, and nothing of W's.
Worked by hand:

    filed       1,800 + 540 + 1,800 + 450 - 180 = 4,410   (available, this month)
    booked      1,800 + 1,800 + 360 + 900       = 4,860   (September's bills)
    matched     1,800 + 1,800 + 360             = 3,960   (booked figures, this month's lines)
    waiting     900                                       (W-1)
    not booked  540 - 180                       = 360     (KA's second invoice, less its note)
"""

import json

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import PartyRole

from .gstr2b import Gstr2bLine, Gstr2bStatement, keep, normalise, read, reconcile
from .tests import D, DAY, GstReturnTestCase, gstin

KA = gstin("29AABCK2222B1Z")
MH = gstin("27AABCM1111A1Z")
DD = gstin("27AABCD9999D1Z")
W = gstin("29AABCW7777W1Z")


def inv(number, day, taxable, igst="0", cgst="0", sgst="0", **extra):
    taxable, igst, cgst, sgst = (D(value) for value in (taxable, igst, cgst, sgst))
    return {"inum": number, "idt": day, "val": str(taxable + igst + cgst + sgst), "pos": "27", "rev": "N",
            "itcavl": "Y", "rsn": "", "items": [{"num": 1, "rt": 18, "txval": str(taxable), "igst": str(igst),
                                                 "cgst": str(cgst), "sgst": str(sgst), "cess": 0}], **extra}


def portal_json(period="092026", our_gstin=None, b2b=(), cdnr=(), **more):
    data = {"gstin": our_gstin or gstin("27AABCD1234E1Z"), "rtnprd": period, "gendt": "14-10-2026",
            "docdata": {"b2b": list(b2b), "cdnr": list(cdnr), **more}}
    return json.dumps({"data": data})


SEPTEMBER_2B = portal_json(
    b2b=[
        {"ctin": KA, "trdnm": "KA Mills", "inv": [inv("INV-27-12", "10-09-2026", "10000", igst="1800"),
                                                  inv("INV-27-13", "12-09-2026", "3000", igst="540")]},
        {"ctin": MH, "trdnm": "MH Traders", "inv": [inv("MH-7", "10-09-2026", "10000", cgst="900", sgst="900")]},
        {"ctin": DD, "trdnm": "D Polymers", "inv": [inv("D-5", "10-09-2026", "2500", cgst="225", sgst="225")]},
    ],
    cdnr=[{"ctin": KA, "trdnm": "KA Mills", "nt": [{"ntnum": "CN-9", "nttyp": "C", "dt": "20-09-2026", "val": "1180",
                                                     "rev": "N", "itcavl": "Y", "rsn": "",
                                                     "items": [{"num": 1, "rt": 18, "txval": 1000, "igst": 180,
                                                                "cgst": 0, "sgst": 0, "cess": 0}]}]}],
    isd=[{"ctin": "27AAAAA0000A1Z5", "doclist": []}],
)


class Gstr2bTestCase(GstReturnTestCase):
    def setUp(self):
        super().setUp()
        self.ka = self.party("KA", role=PartyRole.VENDOR, gstin=KA, gst_state="29")
        self.mh = self.party("MH", role=PartyRole.VENDOR, gstin=MH, gst_state="27")
        self.dd = self.party("DD", role=PartyRole.VENDOR, gstin=DD, gst_state="27")
        self.w = self.party("W", role=PartyRole.VENDOR, gstin=W, gst_state="29")
        self.bills = {
            "ka": self.buy(self.ka, "10000", taxes=[self.igst], reference="INV/27/0012"),
            "mh": self.buy(self.mh, "10000", reference="MH-7"),
            "dd": self.buy(self.dd, "2000", reference="D-5"),
            "w": self.buy(self.w, "5000", taxes=[self.igst], reference="W-1"),
        }


class ReadingTests(Gstr2bTestCase):
    def test_the_portals_json(self):
        header, lines, skipped = read(SEPTEMBER_2B)
        self.assertEqual(header, {"gstin": gstin("27AABCD1234E1Z"), "period": "2026-09",
                                  "generated_on": DAY.replace(month=10, day=14)})
        self.assertEqual([(row["supplier_gstin"], row["kind"], row["number"], row["taxable"], row["igst"], row["value"])
                          for row in lines],
                         [(KA, "invoice", "INV-27-12", D("10000.00"), D("1800.00"), D("11800.00")),
                          (KA, "invoice", "INV-27-13", D("3000.00"), D("540.00"), D("3540.00")),
                          (MH, "invoice", "MH-7", D("10000.00"), D("0.00"), D("11800.00")),
                          (DD, "invoice", "D-5", D("2500.00"), D("0.00"), D("2950.00")),
                          (KA, "credit_note", "CN-9", D("1000.00"), D("180.00"), D("1180.00"))])
        self.assertEqual(skipped, ["isd: 1 supplier(s) not read"])

    def test_a_sheet_saved_as_csv_one_row_a_rate(self):
        text = ("Goods and Services Tax - GSTR-2B,,,,\n"
                "Taxable inward supplies received from registered persons,,,,\n"
                "GSTIN of supplier,Trade/Legal name of the Supplier,Invoice number,Invoice type,Invoice Date,"
                "Invoice Value(₹),Place of supply,Supply Attract Reverse Charge,Rate(%),Taxable Value (₹),"
                "Integrated Tax(₹),Central Tax(₹),State/UT Tax(₹),Cess(₹),GSTR-1/1A/IFF/GSTR-5 Period,"
                "GSTR-1/1A/IFF/GSTR-5 Filing Date,ITC Availability,Reason,Applicable % of Tax Rate,Source,IRN,IRN Date\n"
                f'{KA},KA Mills,INV-27-12,Regular,10-09-2026,"11,800.00",Maharashtra,No,18,"5,000.00",900.00,0,0,0,'
                "Sep-26,11-10-2026,Yes,,100%,,,\n"
                f'{KA},KA Mills,INV-27-12,Regular,10-09-2026,"11,800.00",Maharashtra,No,18,"5,000.00",900.00,0,0,0,'
                "Sep-26,11-10-2026,Yes,,100%,,,\n")
        header, lines, _ = read(text)
        self.assertEqual(header["period"], "")
        self.assertEqual([(row["number"], row["date"], row["taxable"], row["igst"], row["value"], row["itc_available"])
                          for row in lines], [("INV-27-12", DAY, D("10000.00"), D("1800.00"), D("11800.00"), True)])

    def test_what_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "is empty"):
            read("  ")
        with self.assertRaisesMessage(ValidationError, "no docdata"):
            read('{"hello": 1}')
        with self.assertRaisesMessage(ValidationError, "not a GSTR-2B download"):
            read('{"data": {"gstin": "x"}}')
        with self.assertRaisesMessage(ValidationError, "names the supplier's GSTIN"):
            read("a,b\n1,2\n")
        with self.assertRaisesMessage(ValidationError, "No column names the taxable"):
            read("GSTIN of supplier,Invoice number\nx,1\n")
        with self.assertRaisesMessage(ValidationError, "for GSTIN 29ZZZZZ"):
            keep("2026-09", portal_json(our_gstin="29ZZZZZ9999Z1Z5"))
        with self.assertRaisesMessage(ValidationError, "is 2026-09's GSTR-2B, not 2026-08's"):
            keep("2026-08", SEPTEMBER_2B)
        with self.assertRaisesMessage(ValidationError, "Say which month"):
            keep("", "GSTIN of supplier,Invoice number,Taxable Value\nx,1,5\n")
        with self.assertRaisesMessage(ValidationError, "holds no supplier invoices"):
            keep("2026-09", portal_json())

    def test_kept_once_and_replaced_only_when_said(self):
        statement, skipped = keep("", SEPTEMBER_2B)
        self.assertEqual((statement.period, statement.gstin, statement.lines.count(), skipped),
                         ("2026-09", gstin("27AABCD1234E1Z"), 5, ["isd: 1 supplier(s) not read"]))
        with self.assertRaisesMessage(ValidationError, "already kept, with 5 line(s)"):
            keep("2026-09", SEPTEMBER_2B)
        again, _ = keep("2026-09", portal_json(b2b=[{"ctin": MH, "trdnm": "MH", "inv": [inv("MH-7", "10-09-2026", "10000", cgst="900", sgst="900")]}]),
                        replace=True)
        self.assertEqual((Gstr2bStatement.objects.count(), again.lines.count(), Gstr2bLine.objects.count()), (1, 1, 1))

    def test_numbers_typed_differently_are_one_number(self):
        self.assertEqual(normalise("INV/2026-27/0012"), normalise("INV-2026-27-12"))
        self.assertEqual(normalise(" inv 0012 "), "INV12")
        self.assertNotEqual(normalise("INV-12"), normalise("INV-13"))
        self.assertEqual(normalise(""), "")


class MatchingTests(Gstr2bTestCase):
    def test_the_month_against_the_books(self):
        keep("", SEPTEMBER_2B)
        report = reconcile("2026-09")
        self.assertEqual(report["statement"]["lines"], 5)
        self.assertEqual(report["notes"], [])
        self.assertEqual([(row["bill"]["reference"], row["line"]["number"]) for row in report["matched"]],
                         [("INV/27/0012", "INV-27-12"), ("MH-7", "MH-7")])
        self.assertEqual([(row["bill"]["reference"], row["line"]["number"], row["difference"]["taxable"],
                           row["difference"]["cgst"]) for row in report["differs"]],
                         [("D-5", "D-5", D("500.00"), D("45.00"))])
        self.assertEqual([row["bill"]["reference"] for row in report["not_in_2b"]], ["W-1"])
        self.assertEqual([(row["line"]["number"], row["line"]["kind"]) for row in report["not_booked"]],
                         [("CN-9", "credit_note"), ("INV-27-13", "invoice")])
        self.assertEqual(report["totals"], {"filed": D("4410.00"), "booked": D("4860.00"), "matched": D("3960.00"),
                                            "waiting": D("900.00"), "not_booked": D("360.00")})

    def test_the_same_document_of_that_day_for_that_value_when_the_number_was_mistyped(self):
        keep("", portal_json(b2b=[{"ctin": W, "trdnm": "W", "inv": [inv("WX-99", "10-09-2026", "5000", igst="900")]}]))
        report = reconcile("2026-09")
        self.assertEqual([(row["bill"]["reference"], row["line"]["number"]) for row in report["matched"]],
                         [("W-1", "WX-99")])
        self.assertEqual([row["bill"]["reference"] for row in report["not_in_2b"]], ["INV/27/0012", "MH-7", "D-5"])

    def test_carried_across_the_year(self):
        # August's 2B already carried KA's invoice; September's does not
        # repeat it, and September still counts it as matched, not waiting.
        keep("", portal_json(period="082026", b2b=[{"ctin": KA, "trdnm": "KA", "inv": [inv("INV-27-12", "10-09-2026", "10000", igst="1800")]}]))
        keep("", portal_json(b2b=[{"ctin": MH, "trdnm": "MH", "inv": [inv("MH-7", "10-09-2026", "10000", cgst="900", sgst="900")]}]))
        report = reconcile("2026-09")
        self.assertEqual([(row["bill"]["reference"], row["line"]["statement"]) for row in report["matched"]],
                         [("INV/27/0012", "2026-08"), ("MH-7", "2026-09")])
        self.assertEqual(report["totals"]["matched"], D("1800.00"))  # September's own line
        self.assertEqual(report["totals"]["waiting"], D("1260.00"))  # D-5 (360) and W-1 (900)
        # A month with no 2B kept says so, and every bill waits.
        october = reconcile("2026-10")
        self.assertEqual(october["notes"], ["No GSTR-2B is kept for 2026-10: every bill of the month reads as waiting."])
        self.assertEqual([row["bill"]["reference"] for row in october["not_in_2b"]], ["D-5", "W-1"])

    def test_suppliers_outside_the_2b_are_said_not_matched(self):
        composition = self.party("COMP", role=PartyRole.VENDOR, gstin=gstin("27AABCC3333C1Z"), gst_state="27",
                                 gst_registration="composition")
        self.buy(composition, "700", taxes=[self.zero], reference="C-1")
        keep("", SEPTEMBER_2B)
        report = reconcile("2026-09")
        self.assertEqual(report["notes"], ["1 bill(s) from composition or SEZ suppliers are outside the 2B's supplier "
                                           "tables and are not matched."])
        self.assertNotIn("C-1", [row["bill"]["reference"] for row in report["not_in_2b"]])


class ApiTests(Gstr2bTestCase):
    def as_(self, role):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_officer_keeps_the_file_reads_the_match_and_takes_it_back(self):
        officer = self.as_("GST Officer")
        kept = officer.post("/api/gst/gstr2b/upload/", {"text": SEPTEMBER_2B}, format="json")
        self.assertEqual(kept.status_code, 201, kept.content)
        self.assertEqual((kept.json()["period"], kept.json()["line_count"], kept.json()["kept_by"], kept.json()["skipped"]),
                         ("2026-09", 5, "gst_officer", ["isd: 1 supplier(s) not read"]))
        again = officer.post("/api/gst/gstr2b/upload/", {"text": SEPTEMBER_2B, "period": "2026-09"}, format="json")
        self.assertEqual((again.status_code, "already kept" in again.json()["period"][0]), (400, True), again.content)
        self.assertEqual(officer.post("/api/gst/gstr2b/upload/", {"text": SEPTEMBER_2B, "replace": True},
                                      format="json").status_code, 201)
        match = officer.get("/api/gst/gstr2b/match/", {"period": "2026-09"})
        self.assertEqual(match.status_code, 200, match.content)
        self.assertEqual((match.json()["totals"]["waiting"], len(match.json()["matched"])), ("900.00", 2))
        self.assertEqual(officer.get("/api/gst/gstr2b/match/").status_code, 400)
        rows = officer.get("/api/gst/gstr2b/").json()
        self.assertEqual([(row["period"], row["line_count"]) for row in rows], [("2026-09", 5)])
        self.assertEqual(officer.delete(f"/api/gst/gstr2b/{rows[0]['id']}/").status_code, 204)
        self.assertEqual(officer.get("/api/gst/gstr2b/match/", {"period": "2026-09"}).json()["statement"], None)

    def test_who_may(self):
        books = self.as_("Bookkeeper")
        self.assertEqual(books.get("/api/gst/gstr2b/").status_code, 403)
        self.assertEqual(books.get("/api/gst/gstr2b/match/", {"period": "2026-09"}).status_code, 403)
        self.assertEqual(books.post("/api/gst/gstr2b/upload/", {"text": SEPTEMBER_2B}, format="json").status_code, 403)


class SuppliersCreditNoteTests(GstReturnTestCase):
    """
    KA's bill INV/27/0012 (10,000 + IGST 1,800). A tenth of it goes back: the
    company's debit note is 1,000 + 180. KA files its credit note CN-9 for
    exactly that on 20 September; the debit note records it, and the
    reconciliation pairs the two.
    """

    def setUp(self):
        super().setUp()
        from apps.purchasing.models import Bill

        self.ka = self.party("KA", role=PartyRole.VENDOR, gstin=KA, gst_state="29")
        self.bill = Bill.objects.get(pk=self.buy(self.ka, "10000", taxes=[self.igst], reference="INV/27/0012").pk)
        self.cn9 = {"ntnum": "CN-9", "nttyp": "C", "dt": "20-09-2026", "val": "1180", "rev": "N", "itcavl": "Y",
                    "rsn": "", "items": [{"num": 1, "rt": 18, "txval": 1000, "igst": 180, "cgst": 0, "sgst": 0,
                                          "cess": 0}]}

    def report(self, note):
        period = f"{note.bill_date:%Y-%m}"
        keep("", portal_json(
            period=f"{note.bill_date:%m%Y}",
            b2b=[{"ctin": KA, "trdnm": "KA", "inv": [inv("INV-27-12", "10-09-2026", "10000", igst="1800")]}],
            cdnr=[{"ctin": KA, "trdnm": "KA", "nt": [self.cn9]}]))
        report = reconcile(period)
        return ([row["line"]["number"] for row in report["matched"]],
                [row["bill"]["number"] for row in report["not_in_2b"]],
                [row["line"]["number"] for row in report["not_booked"]])

    def test_a_debit_note_meets_the_suppliers_credit_note(self):
        import datetime

        note = self.bill.create_debit_note(quantities={self.bill.lines.get(): D("0.1")},
                                           supplier_note_number="CN-9", supplier_note_date=datetime.date(2026, 9, 20))
        self.assertEqual((note.total(), note.reference, note.supplier_note_number),
                         (D("1180.00"), "INV/27/0012", "CN-9"))
        self.assertEqual(self.report(note), (["INV-27-12", "CN-9"], [], []))

    def test_typed_differently_it_pairs_on_the_suppliers_date_and_value(self):
        import datetime

        note = self.bill.create_debit_note(quantities={self.bill.lines.get(): D("0.1")},
                                           supplier_note_date=datetime.date(2026, 9, 20))
        self.assertEqual(self.report(note), (["INV-27-12", "CN-9"], [], []))

    def test_a_draft_note_is_given_it_before_it_posts_and_not_after(self):
        from apps.purchasing.models import Bill, BillLine

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ap")
        user.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(user)
        line = self.bill.lines.get()
        draft = Bill.objects.create(vendor=self.ka, bill_date=self.bill.bill_date, payable_account=self.ap,
                                    debits=self.bill, reference=self.bill.reference)
        given = BillLine.objects.create(bill=draft, debits_line=line, item=self.sack, quantity=D("0.1"),
                                        unit_price=D("10000"), expense_account=self.expense)
        given.taxes.set([self.igst])
        url = f"/api/purchasing/bills/{draft.pk}/"
        edited = client.patch(url, {"supplier_note_number": "CN-9", "supplier_note_date": "2026-09-20"},
                              format="json")
        self.assertEqual(edited.status_code, 200, edited.content)
        posted = client.post(f"{url}post_bill/", {}, format="json")
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertEqual(client.patch(url, {"supplier_note_number": "CN-10"}, format="json").status_code, 400)
        self.assertEqual(self.report(Bill.objects.get(pk=draft.pk)), (["INV-27-12", "CN-9"], [], []))

    def test_made_over_the_api_with_it(self):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ap")
        user.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(f"/api/purchasing/bills/{self.bill.pk}/debit_note/", {
            "quantities": {str(self.bill.lines.get().pk): "0.1"}, "supplier_note_number": "CN-9",
            "supplier_note_date": "2026-09-20"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["supplier_note_number"], response.json()["supplier_note_date"]),
                         ("CN-9", "2026-09-20"))

    def test_the_refusals(self):
        import datetime

        from django.utils import timezone

        from apps.purchasing.models import Bill

        line = self.bill.lines.get()
        for given, said in (({"supplier_note_date": datetime.date(2026, 9, 1)}, "is not dated 2026-09-01"),
                            ({"supplier_note_date": timezone.localdate() + datetime.timedelta(days=1)},
                             "that day has not come")):
            with self.subTest(given=given), self.assertRaisesMessage(ValidationError, said):
                self.bill.create_debit_note(quantities={line: D("0.1")}, supplier_note_number="CN-9", **given)
        with self.assertRaisesMessage(ValidationError, "Only a debit note records"):
            Bill.objects.create(vendor=self.ka, bill_date=DAY, payable_account=self.ap, supplier_note_number="CN-1")
        from django.db import IntegrityError

        self.bill.create_debit_note(quantities={line: D("0.1")}, supplier_note_number="CN-9")
        with self.assertRaises(IntegrityError):  # one debit note answers one credit note of a supplier
            self.bill.create_debit_note(quantities={line: D("0.1")}, supplier_note_number="CN-9")
