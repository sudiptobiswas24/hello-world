import datetime
from decimal import Decimal as D
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from apps.gst.tests import GstReturnTestCase
from apps.core.models import PartyRole as Role


def show(*a):
    print("\nRV", *a)


class O57(GstReturnTestCase):
    def setUpData(self):
        from apps.gst.tests_gstr2b import KA
        self.KA = KA
        ka = self.party("KA", role=Role.VENDOR, gstin=KA, gst_state="29")
        bill = self.buy(ka, "10000", taxes=[self.igst], reference="INV/27/0012")
        return ka, bill

    def recon(self, note, cn_date="20-09-2026"):
        from apps.gst.gstr2b import keep, reconcile
        from apps.gst.tests_gstr2b import KA, inv, portal_json
        period = f"{note.bill_date:%Y-%m}"
        keep("", portal_json(
            period=f"{note.bill_date:%m%Y}",
            b2b=[{"ctin": KA, "trdnm": "KA", "inv": [inv("INV-27-12", "10-09-2026", "10000", igst="1800")]}],
            cdnr=[{"ctin": KA, "trdnm": "KA", "nt": [{
                "ntnum": "CN-9", "nttyp": "C", "dt": cn_date, "val": "1180", "rev": "N", "itcavl": "Y",
                "rsn": "", "items": [{"num": 1, "rt": 18, "txval": 1000, "igst": 180, "cgst": 0, "sgst": 0, "cess": 0}]}]}]))
        r = reconcile(period)
        return ([x["line"]["number"] for x in r["matched"]], [x["bill"]["number"] for x in r["not_in_2b"]], [x["line"]["number"] for x in r["not_booked"]])

    def test_with_number(self):
        ka, bill = self.setUpData()
        note = bill.create_debit_note(quantities={bill.lines.get(): D("0.1")}, supplier_note_number="CN-9", supplier_note_date=datetime.date(2026, 9, 20))
        show("with number:", self.recon(note))

    def test_number_only_other_date(self):
        ka, bill = self.setUpData()
        note = bill.create_debit_note(quantities={bill.lines.get(): D("0.1")}, supplier_note_number="cn 9")
        show("number 'cn 9' (normalised) no date:", self.recon(note))

    def test_edit_after_post(self):
        ka, bill = self.setUpData()
        note = bill.create_debit_note(quantities={bill.lines.get(): D("0.1")}, supplier_note_number="CN-9", supplier_note_date=datetime.date(2026, 9, 20))
        note.supplier_note_number = "CN-10"
        try:
            note.save(); note.refresh_from_db()
            show("posted debit note's supplier number edited to:", note.supplier_note_number, "posted:", note.posted)
        except ValidationError as e:
            show("edit refused", e)
        show("after edit recon (supplier filed CN-9):", self.recon(note))

    def test_two_notes_one_cn(self):
        ka, bill = self.setUpData()
        n1 = bill.create_debit_note(quantities={bill.lines.get(): D("0.1")}, supplier_note_number="CN-9")
        try:
            with transaction.atomic():
                n2 = bill.create_debit_note(quantities={bill.lines.get(): D("0.1")}, supplier_note_number="CN-9")
            show("second note with same number accepted")
        except (IntegrityError, ValidationError) as e:
            show("second refused:", type(e).__name__, str(e)[:120])
        try:
            with transaction.atomic():
                n2 = bill.create_debit_note(quantities={bill.lines.get(): D("0.1")}, supplier_note_number="cn-9")
            show("second note 'cn-9' (case) accepted")
        except (IntegrityError, ValidationError) as e:
            show("second 'cn-9' refused:", type(e).__name__)
        # 2 notes without numbers
        n3 = bill.create_debit_note(quantities={bill.lines.get(): D("0.1")})
        n4 = bill.create_debit_note(quantities={bill.lines.get(): D("0.1")})
        show("two notes with no number accepted:", n3.number, n4.number)

    def test_api_patch_posted(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient
        ka, bill = self.setUpData()
        note = bill.create_debit_note(quantities={bill.lines.get(): D("0.1")}, supplier_note_number="CN-9")
        u = User.objects.create_superuser("rv", "rv@x.com", "p")
        c = APIClient(); c.force_authenticate(u)
        r = c.patch(f"/api/purchasing/bills/{note.pk}/", {"supplier_note_number": "CN-77"}, format="json")
        show("PATCH posted note:", r.status_code, str(r.content[:160]))
