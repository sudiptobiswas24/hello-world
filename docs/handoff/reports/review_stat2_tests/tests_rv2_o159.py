"""review_stat2 probes: O159 supplier credit-note number on a posted debit note."""
import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.gst.tests_gstr2b import SuppliersCreditNoteTests as Base
from apps.purchasing.models import Bill

d = datetime.date


def show(*a):
    print("RV2", *a)


class Rv2(Base):
    def note(self):
        return self.bill.create_debit_note(quantities={self.bill.lines.get(): D("0.1")})

    def attempt(self, tag, note, number, day=None):
        try:
            note.record_supplier_note(number, day)
            n = Bill.objects.get(pk=note.pk)
            show(tag, "OK key", n.supplier_note_key)
            return True
        except ValidationError as e:
            show(tag, "REFUSED", str(e)[:100])
            return False
        except Exception as e:  # noqa
            show(tag, "EXC", type(e).__name__, str(e)[:80])
            return None

    def test_a_api(self):
        call_command("setup_roles", verbosity=0)
        n = self.note()
        url = f"/api/purchasing/bills/{n.pk}/supplier_note/"

        def who(role):
            u = User.objects.create_user(role.replace(" ", "") + str(User.objects.count()))
            u.groups.add(Group.objects.get(name=role))
            c = APIClient()
            c.force_authenticate(u)
            return c
        for role in ("Purchasing Clerk", "Bookkeeper", "Controller", "GST Officer", "Warehouse Staff", "AP Manager"):
            r = who(role).post(url, {"supplier_note_number": "CN-77", "supplier_note_date": "2026-09-20"}, format="json")
            show("roles", role, r.status_code)
        c = who("AP Manager")
        for body in ({"supplier_note_number": 123}, {"supplier_note_number": ["x"]}, {"supplier_note_number": "CN-1", "supplier_note_date": "bad"},
                     {"supplier_note_number": "CN-1", "supplier_note_date": 20260920}, {"supplier_note_number": "N" * 65},
                     {"supplier_note_number": "N" * 64, "supplier_note_date": "2026-09-20"}, {"supplier_note_number": "---"},
                     {"supplier_note_number": "CN-2", "supplier_note_date": "2027-01-01"}, {"supplier_note_number": "CN-2", "supplier_note_date": "2020-01-01"}):
            try:
                r = c.post(url, body, format="json")
                show("body", str(body)[:70], r.status_code, str(r.content)[:100])
            except Exception as e:  # noqa
                show("body", str(body)[:70], "EXC", type(e).__name__, str(e)[:80])
        r = who("AP Manager").post(f"/api/purchasing/bills/{self.bill.pk}/supplier_note/", {"supplier_note_number": "CN-5"}, format="json")
        show("on a plain bill", r.status_code, str(r.content)[:100])

    def test_b_uniqueness(self):
        a, b, c3, d4 = self.note(), self.note(), self.note(), self.note()
        self.attempt("b1 'CN-9' 20 Sep 2026", a, "CN-9", d(2026, 9, 20))
        self.attempt("b2 'cn 9' same FY", b, "cn 9", d(2026, 10, 1))
        self.attempt("b3 'CN/009' same FY", b, "CN/009", d(2026, 10, 1))
        self.attempt("b4 'CN9' same FY", b, "CN9", d(2026, 10, 1))
        self.attempt("b5 'CN-9' 2 Apr 2027 (next FY)", b, "CN-9", d(2027, 4, 2))
        self.attempt("b6 'CN-9' no date, debit note dated 2026-09 (this FY)", c3, "CN-9", None)
        # false collision: month/serial numbering
        self.attempt("b7 'CN/11/2' dated 5 Nov 2026", c3, "CN/11/2", d(2026, 11, 5))
        self.attempt("b8 'CN/1/12' dated 5 Jan 2027 (another note, same FY)", d4, "CN/1/12", d(2027, 1, 5))
        from apps.purchasing.models import supplier_note_key as k
        show("keys", k("CN/11/2", d(2026, 11, 5)), k("CN/1/12", d(2027, 1, 5)), k("INV-2026-27-12", d(2026, 9, 1)), k("12", d(2026, 9, 1)), k("0", d(2026, 9, 1)))
        # boundary
        self.attempt("b9 31 Mar 2027 vs 1 Apr 2027 same number: first", d4, "ZZ-1", d(2027, 3, 31))

    def test_c_dateless_boundary(self):
        a, b = self.note(), self.note()
        self.attempt("c1 'ZZ-1' dated 31 Mar 2027", a, "ZZ-1", d(2027, 3, 31))
        self.attempt("c2 'ZZ-1' dated 1 Apr 2027", b, "ZZ-1", d(2027, 4, 1))
        self.attempt("c3 'ZZ-1' re-given with 31 Mar 2027 on b", b, "ZZ-1", d(2027, 3, 31))

    def test_d_false_collision_in_window(self):
        a, b = self.note(), self.note()
        self.attempt("d1 'X/1/12' dated 20 Sep 2026", a, "X/1/12", d(2026, 9, 20))
        self.attempt("d2 'X/11/2' dated 25 Sep 2026 (a different supplier note)", b, "X/11/2", d(2026, 9, 25))
