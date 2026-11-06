"""
January's statement read in from the bank's export rather than keyed:

  bank: +1,000 NEFT Acme (5 Jan, UTR1), -50 service charge (31 Jan);
  opening 0, closing 950: the two rows foot, so the difference is 0.00.

Checked first, nothing kept; kept on the second call; the same file a
second time adds nothing. A row dated in February, a row with no amount,
a row with both sides, stop the file by row number.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from .bank_import import import_lines
from .models import BankStatement, BankStatementLine
from .tests_reconciliation import ReconciliationTestCase

SBI = ("Txn Date,Description,Ref No./Cheque No.,Debit,Credit,Balance\n"
       "05-01-2026,NEFT Acme,UTR1,,1000,1000\n"
       "31-01-2026,Service charge,,50,,950\n")
HDFC = ("Date,Narration,Chq./Ref.No.,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
        "05/01/26,UPI-ACME,UPI123,,1000.00,1000.00\n")
SIGNED = "date,description,amount\n2026-01-31,Service charge,-50\n"


class BankImportTests(ReconciliationTestCase):
    def setUp(self):
        super().setUp()
        self.january = self.statement(closing="950")

    def test_checked_first_then_kept_and_a_second_time_adds_nothing(self):
        checked = import_lines(self.january, SBI)
        self.assertEqual(checked, {"rows": 2, "added": 2, "already_there": 0, "errors": [],
                                   "lines_total": Decimal("950.00"), "difference": Decimal("0.00"), "committed": False})
        self.assertEqual(self.january.lines.count(), 0)
        kept = import_lines(self.january, SBI, commit=True)
        self.assertEqual((kept["added"], kept["committed"]), (2, True))
        self.assertEqual([(line.date.isoformat(), line.description, line.reference, line.amount)
                          for line in self.january.lines.order_by("date")],
                         [("2026-01-05", "NEFT Acme", "UTR1", Decimal("1000.00")),
                          ("2026-01-31", "Service charge", "", Decimal("-50.00"))])
        again = import_lines(self.january, SBI, commit=True)
        self.assertEqual((again["added"], again["already_there"], self.january.lines.count()), (0, 2, 2))

    def test_the_banks_own_headings_and_dates(self):
        kept = import_lines(self.january, HDFC, commit=True)
        line = self.january.lines.get()
        self.assertEqual((kept["difference"], line.date.isoformat(), line.description, line.reference, line.amount),
                         (Decimal("-50.00"), "2026-01-05", "UPI-ACME", "UPI123", Decimal("1000.00")))
        import_lines(self.january, SIGNED, commit=True)
        self.assertEqual(self.january.lines.get(amount=Decimal("-50")).description, "Service charge")

    def test_two_identical_rows_are_two_transactions(self):
        twice = "date,description,amount\n2026-01-09,ATM,-500\n2026-01-09,ATM,-500\n"
        self.assertEqual(import_lines(self.january, twice, commit=True)["added"], 2)
        again = import_lines(self.january, twice, commit=True)
        self.assertEqual((again["added"], again["already_there"], self.january.lines.count()), (0, 2, 2))

    def test_a_wrong_row_stops_the_file_and_names_itself(self):
        bad = ("Txn Date,Description,Ref No./Cheque No.,Debit,Credit\n"
               "05-01-2026,NEFT Acme,UTR1,,1000\n"
               "02-02-2026,Too late,,,10\n"
               "06-01-2026,Both,,5,5\n"
               "07-01-2026,Neither,,,\n"
               "08-01-2026,Nothing,,0,\n")
        report = import_lines(self.january, bad, commit=True)
        self.assertEqual(report["errors"], [
            (3, "txn date", "02-02-2026 is outside the statement (01-01-2026 to 31-01-2026)."),
            (4, "credit", "has both credit and debit; a line is one or the other."),
            (5, "credit", "is required: a line moves money."),
            (6, "credit", "is zero; the bank moved nothing."),
        ])
        self.assertEqual((report["committed"], self.january.lines.count()), (False, 0))
        with self.assertRaisesMessage(ValidationError, "No column names the date"):
            import_lines(self.january, "when,what,amount\n2026-01-05,x,1\n")
        with self.assertRaisesMessage(ValidationError, "No column holds the amount"):
            import_lines(self.january, "date,what\n2026-01-05,x\n")
        BankStatement.objects.filter(pk=self.january.pk).update(closed=True)
        self.january.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "closed"):
            import_lines(self.january, SBI)


class BankImportApiTests(ReconciliationTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.january = self.statement(closing="950")

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_bookkeeper_imports_and_the_clerk_does_not(self):
        url = f"/api/accounting/bank-statements/{self.january.pk}/import_lines/"
        books = self.as_("Bookkeeper")
        checked = books.post(url, {"text": SBI}, format="json")
        self.assertEqual(checked.status_code, 200, checked.content)
        self.assertEqual((checked.json()["added"], checked.json()["committed"], checked.json()["difference"]),
                         (2, False, "0.00"))
        self.assertEqual(BankStatementLine.objects.count(), 0)
        kept = books.post(url, {"text": SBI, "commit": True}, format="json")
        self.assertEqual((kept.status_code, kept.json()["committed"], BankStatementLine.objects.count()), (200, True, 2))
        refused = books.post(url, {"text": SBI + "02-02-2026,Late,,,1,\n", "commit": True}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("Row 4, txn date", refused.json()["text"][0])
        self.assertEqual(books.post(url, {"text": "  "}, format="json").status_code, 400)
        self.assertEqual(self.as_("Purchasing Clerk").post(url, {"text": SBI}, format="json").status_code, 403)
