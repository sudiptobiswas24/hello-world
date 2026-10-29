"""
A month reconciled through the API, as the two people who do it: the
bookkeeper keys the statement in and matches what is obvious; the
controller posts the bank's own charge, corrects a posting to the wrong
account, and signs the month off.

  books:  receipt 1,000 (5 Jan), cheque 300 (6 Jan, not yet cashed)
  bank:   +1,000 (5 Jan), -50 charges (31 Jan); closing 950
  ledger at 31 Jan after the charge: 1,000 - 300 - 50 = 650
  650 less the uncashed cheque (-300) = 950, the bank's figure.
  Before the charge is posted: (700 + 300) - 950 = 50 unexplained.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .models import BankStatementLine, JournalLine
from .tests_reconciliation import ReconciliationTestCase

STATEMENTS, LINES = "/api/accounting/bank-statements/", "/api/accounting/bank-statement-lines/"


class BankingApiTests(ReconciliationTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.received = self.receipt("1000", on=datetime.date(2026, 1, 5))
        self.cheque = self.disbursement("300", on=datetime.date(2026, 1, 6))

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def keyed_in(self, bookkeeper):
        statement = bookkeeper.post(STATEMENTS, {
            "bank_account": self.bank.pk, "reference": "JAN", "start_date": "2026-01-01",
            "end_date": "2026-01-31", "opening_balance": "0", "closing_balance": "950"}, format="json")
        self.assertEqual(statement.status_code, 201, statement.content)
        pk = statement.json()["id"]
        rows = [bookkeeper.post(LINES, {"statement": pk, "date": day, "description": words, "amount": amount},
                                format="json").json()
                for day, words, amount in (("2026-01-05", "NEFT Acme", "1000"),
                                           ("2026-01-31", "Service charge", "-50"))]
        return pk, rows

    def test_a_month_matched_posted_corrected_and_closed(self):
        bookkeeper, controller = self.as_("Bookkeeper"), self.as_("Controller")
        pk, (deposit, charge) = self.keyed_in(bookkeeper)

        matched = bookkeeper.post(f"{STATEMENTS}{pk}/auto_match/", {}, format="json")
        self.assertEqual(matched.json(), {"matched": 1})
        self.assertEqual(BankStatementLine.objects.get(pk=deposit["id"]).payment, self.received)
        report = bookkeeper.get(f"{STATEMENTS}{pk}/reconciliation/").json()
        self.assertEqual((report["ledger_balance"], report["unpresented_total"], report["unresolved_lines"],
                          report["difference"]), ("700.00", "-300.00", 1, "50.00"))
        self.assertEqual([row["id"] for row in report["unpresented"]], [self.cheque.pk])

        posting = f"{STATEMENTS}{pk}/post_line/"
        self.assertEqual(bookkeeper.post(posting, {"line": charge["id"], "account": self.charges.pk},
                                         format="json").status_code, 403)
        wrong = controller.post(posting, {"line": charge["id"], "account": self.ar.pk}, format="json")
        self.assertEqual(wrong.status_code, 200, wrong.content)
        undone = controller.post(f"{LINES}{charge['id']}/reverse_posting/", {}, format="json")
        self.assertEqual(undone.status_code, 200, undone.content)
        self.assertFalse(undone.json()["resolved"])
        right = controller.post(posting, {"line": charge["id"], "account": self.charges.pk}, format="json")
        self.assertEqual(right.status_code, 200, right.content)

        report = controller.get(f"{STATEMENTS}{pk}/reconciliation/").json()
        self.assertEqual((report["ledger_balance"], report["unresolved_lines"], report["difference"]),
                         ("650.00", 0, "0.00"))
        ar_left = sum(row.debit - row.credit for row in JournalLine.objects.filter(
            account=self.ar, entry__posted=True, entry__memo__contains="Service charge"))
        self.assertEqual(ar_left, Decimal("0"))

        self.assertEqual(bookkeeper.post(f"{STATEMENTS}{pk}/close/", {}, format="json").status_code, 403)
        closed = controller.post(f"{STATEMENTS}{pk}/close/", {}, format="json")
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertTrue(closed.json()["closed"])
        late = bookkeeper.patch(f"{LINES}{deposit['id']}/", {"description": "x"}, format="json")
        self.assertEqual(late.status_code, 400)
        self.assertIn("closed", late.content.decode())

    def test_an_explained_line_is_not_changed_or_dropped(self):
        bookkeeper, controller = self.as_("Bookkeeper"), self.as_("Controller")
        pk, (deposit, charge) = self.keyed_in(bookkeeper)
        bookkeeper.post(f"{STATEMENTS}{pk}/auto_match/", {}, format="json")
        moved = bookkeeper.patch(f"{LINES}{deposit['id']}/", {"amount": "1100"}, format="json")
        self.assertEqual(moved.status_code, 400)
        self.assertIn("explained already", moved.content.decode())
        described = bookkeeper.patch(f"{LINES}{deposit['id']}/", {"description": "NEFT Acme Ltd"}, format="json")
        self.assertEqual(described.status_code, 200, described.content)

        controller.post(f"{STATEMENTS}{pk}/post_line/", {"line": charge["id"], "account": self.charges.pk},
                        format="json")
        dropped = controller.delete(f"{LINES}{charge['id']}/")
        self.assertEqual(dropped.status_code, 400)
        self.assertIn("reverse it first", dropped.content.decode())
        statement_gone = controller.delete(f"{STATEMENTS}{pk}/")
        self.assertEqual(statement_gone.status_code, 400)
        self.assertTrue(BankStatementLine.objects.filter(pk=charge["id"]).exists())

    def test_one_payment_on_one_line(self):
        bookkeeper = self.as_("Bookkeeper")
        pk, (deposit, _charge) = self.keyed_in(bookkeeper)
        bookkeeper.post(f"{STATEMENTS}{pk}/match/", {"line": deposit["id"], "payment": self.received.pk},
                        format="json")
        unmatched = bookkeeper.post(f"{LINES}{deposit['id']}/unmatch/", {}, format="json")
        self.assertFalse(unmatched.json()["resolved"])
        again = bookkeeper.post(f"{LINES}{deposit['id']}/unmatch/", {}, format="json")
        self.assertEqual(again.status_code, 400)
        bookkeeper.post(f"{STATEMENTS}{pk}/match/", {"line": deposit["id"], "payment": self.received.pk},
                        format="json")
        twin = bookkeeper.post(LINES, {"statement": pk, "date": "2026-01-05", "amount": "1000"}, format="json").json()
        refused = bookkeeper.post(f"{STATEMENTS}{pk}/match/", {"line": twin["id"], "payment": self.received.pk},
                                  format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("already matched", refused.content.decode())
        other = bookkeeper.post(STATEMENTS, {
            "bank_account": self.bank.pk, "start_date": "2026-02-01", "end_date": "2026-02-28",
            "opening_balance": "950", "closing_balance": "950"}, format="json").json()
        astray = bookkeeper.post(f"{STATEMENTS}{other['id']}/match/", {"line": twin["id"], "payment": self.cheque.pk},
                                 format="json")
        self.assertEqual(astray.status_code, 400)
        self.assertIn("another statement", astray.content.decode())
