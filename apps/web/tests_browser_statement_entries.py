"""
A bank line matched, on the statement screen, to money a document already booked through the bank (O230,
O156), as the controller does it; read back from the database.

  books: a challan's entry moves -500.00 out of 1010 on 20 March. bank: -500.00 on 20 March.
"""

import datetime
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.models import (BANK_MOVEMENTS, Account, AccountType, BankStatement, BankStatementLine,
                                    JournalEntry, JournalLine)

from .tests_browser import BrowserTestCase


class StatementEntriesInTheBrowserTests(BrowserTestCase):
    def open_form(self, page, label):
        page.get_by_role("button", name=label).click()
        return page.get_by_role("form", name=label)

    def test_a_line_is_matched_to_an_entry_a_document_booked(self):
        expense = Account.objects.create(code="2250", name="TDS payable", account_type=AccountType.LIABILITY)
        entry = JournalEntry.objects.create(date=datetime.date(2026, 3, 20), reference="CHALLAN-1", memo="TDS for February")
        JournalLine.objects.create(entry=entry, account=expense, debit=Decimal("500"))
        JournalLine.objects.create(entry=entry, account=self.bank, credit=Decimal("500"))
        entry.post()
        movement = lambda: JournalEntry.objects.filter(pk=entry.pk)  # noqa: E731 - a document's bank movement
        BANK_MOVEMENTS.append(movement)
        self.addCleanup(BANK_MOVEMENTS.remove, movement)
        statement = BankStatement.objects.create(
            bank_account=self.bank, start_date=datetime.date(2026, 3, 1), end_date=datetime.date(2026, 3, 31),
            opening_balance=Decimal("0"), closing_balance=Decimal("-500"))
        line = BankStatementLine.objects.create(statement=statement, date=datetime.date(2026, 3, 20),
                                                description="TDS challan", amount=Decimal("-500"))

        page = self.sign_in(self.person("Controller"), f"/app/accounts/bank-statements/{statement.pk}")
        form = self.open_form(page, "Match an entry")
        form.get_by_label("Line").select_option(label="2026-03-20 · TDS challan · -500.00")
        form.get_by_label("Entry").select_option(label="CHALLAN-1 · 2026-03-20 · -500.00")
        form.get_by_role("button", name="Match an entry").click()
        expect(page.locator("main")).to_contain_text(f"Booked, JE-{entry.pk}")
        line.refresh_from_db()
        self.assertEqual(line.booked_entry, entry)
        self.assertEqual(self.problems, [])
