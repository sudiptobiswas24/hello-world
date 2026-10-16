"""
The books asked to prove themselves. On a clean set (an opening invoice,
a receipt partly applied) every probe agrees; a hand journal to the
receivable account, an entry that does not balance, an entry posted
into a closed month after it closed and an invoice number taken and
never issued are each found, named and counted in the Controller's
inbox. The server's side: /healthz/ answers 200 to nobody in particular
while the backups are fresh, 503 naming the trouble when they are not.
"""

import datetime
import os
import tempfile
import time
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import (
    Account,
    AccountingPeriod,
    AccountType,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentDirection,
)
from apps.core.models import DocumentSequence, DocumentSequenceYear
from apps.imports.importer import run
from apps.imports.tests import ImportTestCase
from apps.sales.models import Invoice, InvoicePayment

from .checks import inbox
from .health import backups, integrity, server

D = Decimal


class HealthTestCase(ImportTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        call_command("setup_roles", verbosity=0)
        self.today = timezone.localdate()
        self.expense = Account.objects.create(code="6900", name="Sundry", account_type=AccountType.EXPENSE)
        # Scenario: the fixture's 500 widgets at 4 are 2,000 of stock the
        # ledger never saw; booked here, as opening stock would be. One
        # open invoice of 20,000 from the old system: receivables ledger
        # 20,000 = documents 20,000.
        self.hand_journal(self.inventory, self.opening, "2000")
        report = run("open_invoices", f"customer,reference,date,amount\nC-1,OLD/1,{self.today - datetime.timedelta(days=40)},20000\n",
                     commit=True, against="3900")
        self.assertTrue(report.committed, report.errors)
        self.invoice = Invoice.objects.get(reference="OLD/1")

    def finding(self, key, fresh=True):
        return next(row for row in integrity(self.today, fresh=fresh)["findings"] if row["key"] == key)

    def hand_journal(self, debit_account, credit_account, amount, day=None, posted_at=None):
        """A balanced entry posted by hand, straight past the guards."""
        entry = JournalEntry.objects.create(date=day or self.today, memo="by hand", posted=True,
                                            posted_at=posted_at or timezone.now())
        JournalLine.objects.bulk_create([
            JournalLine(entry=entry, account=debit_account, debit=D(amount)),
            JournalLine(entry=entry, account=credit_account, credit=D(amount)),
        ])
        return entry

    def controller(self):
        user = User.objects.create_user("controller")
        user.groups.add(Group.objects.get(name="Controller"))
        return user


class ProbeTests(HealthTestCase):
    def test_a_clean_set_of_books_agrees_everywhere(self):
        # 3,000 received on account, 1,000 of it applied: the invoice owes
        # 19,000, the receipt has 2,000 unapplied, the ledger holds 17,000.
        receipt = Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT,
                                         payment_date=self.today, amount=D("3000"), currency=self.usd,
                                         bank_account=self.bank, counterpart_account=self.ar)
        receipt.post()
        InvoicePayment.objects.create(invoice=self.invoice, payment=receipt, amount=D("1000"))
        self.assertEqual(self.balance(self.ar), D("17000"))
        self.assertEqual(self.invoice.amount_due(), D("19000"))
        findings = integrity(self.today, fresh=True)["findings"]
        self.assertEqual([(row["key"], row["ok"], row["count"]) for row in findings],
                         [("trial_balance", True, 0), ("entries_balanced", True, 0), ("receivables", True, 0),
                          ("payables", True, 0), ("stock", True, 0), ("negative_stock", True, 0),
                          ("closed_periods", True, 0), ("invoice_numbers", True, 0)])
        self.assertEqual(self.finding("receivables")["rows"][0]["label"],
                         "1100 AR: ledger 17,000.00, documents 17,000.00")
        self.assertEqual([row["key"] for row in inbox(self.controller(), self.today) if row["key"] == "books_disagree"], [])

    def test_a_hand_journal_to_the_control_account_is_found_and_counted(self):
        self.hand_journal(self.ar, self.opening, "500")
        found = self.finding("receivables")
        self.assertEqual((found["ok"], found["count"]), (False, 1))
        self.assertEqual(found["rows"][0]["label"], "1100 AR: ledger 20,500.00, documents 20,000.00, difference 500.00")
        rows = {row["key"]: row for row in inbox(self.controller(), self.today)}
        self.assertEqual((rows["books_disagree"]["count"], rows["books_disagree"]["href"]), (1, "/settings/health"))

    def test_the_payables_mirror(self):
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        vendor = Party.objects.create(code="V-1", name="Granules", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        report = run("open_bills", f"vendor,reference,date,amount\nV-1,GH/7,{self.today},7000\n", commit=True, against="3900")
        self.assertTrue(report.committed, report.errors)
        self.assertEqual(self.finding("payables")["ok"], True)
        self.hand_journal(self.opening, self.payable, "250")
        found = self.finding("payables")
        self.assertEqual((found["ok"], found["count"]), (False, 1))
        self.assertEqual(found["rows"][0]["label"], "2000 Payables: ledger 7,250.00, documents 7,000.00, difference 250.00")

    def test_an_entry_that_does_not_balance_is_found(self):
        entry = JournalEntry.objects.create(date=self.today, memo="lopsided", posted=True, posted_at=timezone.now())
        JournalLine.objects.bulk_create([JournalLine(entry=entry, account=self.expense, debit=D("10")),
                                         JournalLine(entry=entry, account=self.opening, credit=D("7"))])
        found = self.finding("entries_balanced")
        self.assertEqual((found["ok"], found["count"], found["rows"][0]["href"]), (False, 1, f"/accounts/journals/{entry.pk}"))
        self.assertIn("Dr 10.00, Cr 7.00", found["rows"][0]["label"])
        self.assertEqual(self.finding("trial_balance")["ok"], False)

    def test_posting_into_a_closed_month_after_it_closed_is_found(self):
        start = (self.today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        end = self.today.replace(day=1) - datetime.timedelta(days=1)
        period = AccountingPeriod.objects.create(name="Last month", start_date=start, end_date=end)
        # Posted before the close: fine.
        self.hand_journal(self.expense, self.opening, "40", day=start)
        period.close()
        self.assertEqual(self.finding("closed_periods")["ok"], True)
        late = self.hand_journal(self.expense, self.opening, "60", day=end)
        found = self.finding("closed_periods")
        self.assertEqual((found["ok"], found["count"], found["rows"][0]["href"]), (False, 1, f"/accounts/journals/{late.pk}"))
        self.assertIn("into Last month", found["rows"][0]["label"])

    def test_a_number_taken_and_never_issued_is_found(self):
        sequence = DocumentSequence.objects.get(code="sales.invoice")  # made by the import's first post
        counter = DocumentSequenceYear.objects.get(sequence=sequence, year=self.invoice.invoice_date.year)
        self.assertEqual(self.finding("invoice_numbers")["ok"], True)
        counter.next_number += 2  # two taken; the next invoice shows where
        counter.save()
        Invoice.objects.create(customer=self.customer, invoice_date=self.today, receivable_account=self.ar,
                               currency=self.usd, number=sequence._format(counter.next_number - 1, counter.year))
        found = self.finding("invoice_numbers")
        self.assertEqual((found["ok"], found["count"]), (False, 1))
        self.assertEqual(found["rows"], [{"label": sequence._format(counter.next_number - 2, counter.year),
                                          "href": "/sales/invoices"}])

    def test_a_probe_that_crashes_is_a_finding_not_a_blank(self):
        from .health import Probe

        def crash(day):
            raise RuntimeError("boom")

        with mock.patch("apps.web.health.PROBES", [Probe("trial_balance", "Trial balance", crash)]):
            found = self.finding("trial_balance")
        self.assertEqual((found["ok"], found["detail"]), (False, "The check could not run: RuntimeError: boom"))

    def test_the_answer_is_kept_ten_minutes_for_the_inbox(self):
        self.finding("receivables", fresh=True)
        self.hand_journal(self.ar, self.opening, "500")
        self.assertEqual(self.finding("receivables", fresh=False)["ok"], True)
        self.assertEqual(self.finding("receivables", fresh=True)["ok"], False)


class ApiTests(HealthTestCase):
    def test_the_controller_reads_it_and_a_bookkeeper_may_not(self):
        client = APIClient()
        client.force_authenticate(self.controller())
        got = client.get("/api/web/health/", {"fresh": "true"})
        self.assertEqual(got.status_code, 200, got.content)
        body = got.json()
        self.assertEqual([row["key"] for row in body["books"]][:3], ["trial_balance", "entries_balanced", "receivables"])
        self.assertEqual(body["server"]["database"], "ok")
        checks = {row["key"]: row["count"] for row in body["checks"]}
        self.assertEqual((checks["books_disagree"], checks["invoices_overdue"]), (0, 1))
        self.assertIn("items_without_hsn", checks)  # zero included: what was looked at is on the page
        books = User.objects.create_user("books")
        books.groups.add(Group.objects.get(name="Bookkeeper"))
        client.force_authenticate(books)
        self.assertEqual(client.get("/api/web/health/").status_code, 403)


@mock.patch("apps.web.health._migrations_pending", return_value=0)
class ServerTests(HealthTestCase):
    def test_healthz_answers_nobody_in_particular(self, _pending):
        got = Client().get("/healthz/")
        self.assertEqual((got.status_code, got.json()), (200, {"ok": True, "problems": []}))
        self.assertEqual(got["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")

    def test_backups_fresh_stale_and_missing(self, _pending):
        with tempfile.TemporaryDirectory() as folder, override_settings(BACKUP_DIR=folder):
            got = Client().get("/healthz/")
            self.assertEqual((got.status_code, got.json()["problems"]), (503, ["no backup found"]))
            dump = os.path.join(folder, "erp_2026-10-06_0200.dump")
            open(dump, "wb").close()
            self.assertEqual(Client().get("/healthz/").status_code, 200)
            kept = backups()
            self.assertEqual((kept["newest"], kept["stale"], kept["age_hours"] < 1), ("erp_2026-10-06_0200.dump", False, True))
            thirty_hours_ago = time.time() - 30 * 3600
            os.utime(dump, (thirty_hours_ago, thirty_hours_ago))
            got = Client().get("/healthz/")
            self.assertEqual((got.status_code, got.json()["problems"]), (503, ["no backup in the last day"]))
            rows = {row["key"]: row["count"] for row in inbox(self.controller(), self.today)}
            self.assertEqual(rows["backup_stale"], 1)
        self.assertEqual(server()["backups"]["configured"], False)

    def test_the_command_prints_every_answer_and_exits_one_on_trouble(self, _pending):
        out = StringIO()
        call_command("check_health", stdout=out)
        self.assertIn("ok    Receivables agree with the invoices: Every control account holds what the invoices say.", out.getvalue())
        self.assertIn("Everything agrees.", out.getvalue())
        self.hand_journal(self.ar, self.opening, "500")
        out = StringIO()
        with self.assertRaisesMessage(CommandError, "1 check(s) failed."):
            call_command("check_health", stdout=out)
        self.assertIn("FAIL  Receivables agree with the invoices", out.getvalue())
        self.assertIn("difference 500.00", out.getvalue())
