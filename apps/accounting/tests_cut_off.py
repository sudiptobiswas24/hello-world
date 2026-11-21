"""
A bank's statements reconciled from a day (O615), and journals keyed by hand
through a bank matched to its lines (O614).

The figures are the scenario table worked by hand before the code
(/home/user/work/g55_scenarios.py in the session that built it): books 75,000
at the cut-off less 11,500 outstanding is the bank's 86,500; October closes at
123,910 against books of 123,210 and 700 not yet presented.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import (
    Account,
    AccountType,
    BankCutOffItem,
    BankStatement,
    JournalEntry,
    JournalLine,
    bank_movements,
)
from .tests_reconciliation import ReconciliationTestCase

D = Decimal
AUG30, SEP1, SEP10, SEP20, SEP25, SEP28, SEP30 = (datetime.date(2026, 8, 30), datetime.date(2026, 9, 1),
                                                  datetime.date(2026, 9, 10), datetime.date(2026, 9, 20),
                                                  datetime.date(2026, 9, 25), datetime.date(2026, 9, 28),
                                                  datetime.date(2026, 9, 30))
OCT1, OCT3, OCT5, OCT10, OCT11, OCT15, OCT18, OCT19, OCT20, OCT30, OCT31 = (
    datetime.date(2026, 10, day) for day in (1, 3, 5, 10, 11, 15, 18, 19, 20, 30, 31))


class CutOffTestCase(ReconciliationTestCase):
    def setUp(self):
        super().setUp()
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.equity = acc("3000", "Opening balances", AccountType.EQUITY)
        self.rent = acc("5100", "Rent", AccountType.EXPENSE)
        self.power = acc("5200", "Power", AccountType.EXPENSE)

    def journal(self, on, memo, amount, against, bank=None):
        """Keyed by hand: `amount` through the bank as the bank sees it, the other side to `against`."""
        amount = D(amount)
        entry = JournalEntry.objects.create(date=on, memo=memo)
        JournalLine.objects.create(entry=entry, account=bank or self.bank, debit=max(amount, D("0")),
                                   credit=max(-amount, D("0")))
        JournalLine.objects.create(entry=entry, account=against, debit=max(-amount, D("0")),
                                   credit=max(amount, D("0")))
        entry.post()
        return entry

    def reconcile_from(self, day, account=None):
        account = account or self.bank
        account.reconciled_from = day
        account.save()
        account.refresh_from_db()
        return account

    def month(self, start, end, opening, closing, account=None):
        return BankStatement.objects.create(bank_account=account or self.bank, start_date=start, end_date=end,
                                            opening_balance=D(opening), closing_balance=D(closing))

    def cheque(self, amount, reference="000101", on=AUG30):
        return BankCutOffItem.objects.create(bank_account=self.bank, date=on, reference=reference,
                                             description="Cheque from before the books", amount=D(amount))

    def september(self):
        """Everything before the cut-off: the opening, two payments cleared or not, the rent."""
        self.opening = self.journal(SEP1, "Opening bank balance", "100000", self.equity)
        self.p1 = self.disbursement("10000", on=SEP10)
        self.p2 = self.disbursement("5000", on=SEP25)
        self.p3 = self.disbursement("2000", on=SEP28)
        self.j1 = self.journal(SEP20, "Rent", "-8000", self.rent)


class TheScenarioTests(CutOffTestCase):
    def build(self):
        self.september()
        self.reconcile_from(OCT1)
        BankCutOffItem.objects.create(bank_account=self.bank, payment=self.p2)
        BankCutOffItem.objects.create(bank_account=self.bank, payment=self.p3)
        self.o1 = self.cheque("-3000", "000101")
        self.o2 = self.cheque("-1500", "000102")
        self.p3.void(memo="Cheque cancelled", on_date=OCT15)
        self.j2 = self.journal(OCT10, "Power bill", "-4000", self.power)
        self.p4 = self.receipt("50000", on=OCT18)
        self.cancel = self.journal(OCT20, "Cheque 000102 cancelled", "1500", self.ap)
        self.o2.cancel(self.cancel)
        self.j3 = self.journal(OCT30, "Petty cash", "-700", self.power)

    def test_october_reconciles_from_the_cut_off_with_nothing_unexplained(self):
        self.build()
        october = self.month(OCT1, OCT31, "86500", "123910")
        presented = self.line(october, "-5000", on=OCT3)
        presented.match(self.p2)  # eight days after it was written: too far for the obvious
        lines = [self.line(october, amount, on=on) for amount, on in
                 (("-3000", OCT5), ("-4000", OCT11), ("50000", OCT19))]
        charges = self.line(october, "-590", on=OCT31)
        self.assertEqual(october.auto_match(), lines)
        for line in lines:
            line.refresh_from_db()
        self.assertEqual([(line.cut_off_item_id, line.booked_entry_id, line.payment_id) for line in lines],
                         [(self.o1.pk, None, None), (None, self.j2.pk, None), (None, None, self.p4.pk)])
        charges.post_to(self.charges)
        report = october.reconciliation()
        self.assertEqual(
            (report["ledger_balance"], report["unpresented"], report["unreturned"], report["outstanding_at_cut_off"],
             report["unpresented_entries"], report["unpresented_total"], report["difference"]),
            (D("123210"), [], [], [], [(self.j3, D("-700.00"))], D("-700.00"), D("0")))
        october.close()

    def test_read_at_the_fifteenth_the_cancelled_cheque_is_still_outstanding(self):
        self.build()
        half = self.month(OCT1, OCT15, "86500", "74500")
        self.line(half, "-5000", on=OCT3).match(self.p2)
        self.line(half, "-3000", on=OCT5).match_cut_off_item(self.o1)
        self.line(half, "-4000", on=OCT11).match_entry(self.j2)
        report = half.reconciliation()
        # p3, listed and voided on the 15th, never reached the bank: neither it nor its void is listed.
        self.assertEqual((report["ledger_balance"], report["unpresented"], report["unreturned"],
                          report["outstanding_at_cut_off"], report["unpresented_entries"], report["difference"]),
                         (D("73000"), [], [], [self.o2], [], D("0")))

    def test_without_the_cut_off_every_september_movement_reads_as_unpresented(self):
        # The defect O615 named, kept as the reason for the day: October alone, reconciled from the first entry.
        self.september()
        october = self.month(OCT1, OCT31, "86500", "86500")
        # From the first entry every movement waits for a line, the opening balance with them: books less all of
        # them is nothing, against the bank's 86,500.
        self.assertEqual(october.reconciliation()["difference"], D("-86500"))
        self.reconcile_from(OCT1)
        # From the day, all of it cleared: books 75,000 against 86,500, the four cheques the bank had not paid.
        self.assertEqual(october.reconciliation()["difference"], D("-11500"))
        for payment in (self.p2, self.p3):
            BankCutOffItem.objects.create(bank_account=self.bank, payment=payment)
        self.cheque("-3000", "000101")
        self.cheque("-1500", "000102")
        self.assertEqual(october.reconciliation()["difference"], D("0"))


class HandJournalsTests(CutOffTestCase):
    """O614: a journal keyed by hand through the bank, explained by the statement line that shows it."""

    def test_a_journal_keyed_by_hand_is_unpresented_until_its_line_is_matched_and_then_the_month_closes(self):
        rent = self.journal(datetime.date(2026, 1, 3), "Rent", "-8000", self.rent)
        statement = self.statement(closing="-8000")
        line = self.line(statement, "-8000", on=datetime.date(2026, 1, 4))
        report = statement.reconciliation()
        self.assertEqual((report["unpresented_entries"], report["unresolved_lines"]),
                         ([(rent, D("-8000.00"))], [line]))
        self.assertEqual(statement.auto_match(), [line])
        self.assertEqual(statement.reconciliation()["difference"], D("0"))
        statement.close()

    def test_a_lines_posting_reversed_is_neither_listed_nor_in_the_way_of_posting_it_again(self):
        statement = self.statement(closing="-25")
        line = self.line(statement, "-25")
        line.post_to(self.ap)
        line.reverse_posting()
        line.refresh_from_db()
        self.assertEqual(statement.unpresented_entries(), [])
        line.post_to(self.charges)
        self.assertEqual(statement.reconciliation()["difference"], D("0"))
        statement.close()

    def test_a_journal_reversed_by_hand_that_no_bank_showed_nets_away(self):
        mistake = self.journal(datetime.date(2026, 1, 3), "Rent keyed twice", "-8000", self.rent)
        mistake.create_reversal(entry_date=datetime.date(2026, 1, 4))
        report = self.statement().reconciliation()
        self.assertEqual((report["unpresented_entries"], report["difference"]), ([], D("0")))

    def test_a_reversed_journal_is_matched_only_after_the_line_that_took_it_back(self):
        transfer = self.journal(datetime.date(2026, 1, 3), "Transfer", "-500", self.other_bank)
        back = transfer.create_reversal(entry_date=datetime.date(2026, 1, 5))
        statement = self.statement()
        out, returned = self.line(statement, "-500", on=datetime.date(2026, 1, 3)), self.line(statement, "500")
        with self.assertRaisesMessage(ValidationError, f"Match the line that took it back to JE-{back.pk} first"):
            out.match_entry(transfer)
        returned.match_entry(back)
        out.match_entry(transfer)
        self.assertEqual(statement.reconciliation()["difference"], D("0"))

    def test_a_journal_matched_to_a_line_is_not_reversed_by_hand(self):
        rent = self.journal(datetime.date(2026, 1, 3), "Rent", "-8000", self.rent)
        statement = self.statement(closing="-8000")
        self.line(statement, "-8000").match_entry(rent)
        with self.assertRaisesMessage(ValidationError, "unmatch it there before reversing it"):
            rent.reverse_by_hand()

    def test_a_payments_own_entry_is_not_a_movement_of_its_own(self):
        paid = self.disbursement("300")
        self.assertFalse(bank_movements(self.bank).filter(pk=paid.journal_entry_id).exists())
        report = self.statement(closing="0").reconciliation()
        self.assertEqual((report["unpresented"], report["unpresented_entries"], report["unpresented_total"]),
                         ([paid], [], D("-300.00")))


class TheDayTests(CutOffTestCase):
    """The day itself (Account.reconciled_from): who may hold one, and when it moves."""

    def test_only_money_is_reconciled_from_a_day(self):
        with self.assertRaisesMessage(ValidationError, "Only a bank, cash or card account"):
            self.reconcile_from(OCT1, self.rent)

    def test_no_statement_starts_before_it_made_or_moved(self):
        self.reconcile_from(OCT1)
        with self.assertRaisesMessage(ValidationError, "starts on or after that day"):
            self.month(SEP1, SEP30, "0", "0")
        october = self.month(OCT1, OCT31, "0", "0")
        october.start_date = SEP30
        with self.assertRaisesMessage(ValidationError, "starts on or after that day"):
            october.save()

    def test_not_after_a_statement_the_account_has(self):
        self.month(SEP1, SEP30, "0", "0")
        with self.assertRaisesMessage(ValidationError, "starts on 2026-09-01, before 2026-10-01"):
            self.reconcile_from(OCT1)

    def test_not_moved_while_a_statement_is_closed(self):
        self.reconcile_from(OCT1)
        self.month(OCT1, OCT31, "0", "0").close()
        with self.assertRaisesMessage(ValidationError, "is closed, reconciled from 2026-10-01"):
            self.reconcile_from(datetime.date(2026, 9, 15))

    def test_not_cleared_while_items_are_listed_nor_set_on_or_before_one(self):
        self.reconcile_from(OCT1)
        self.cheque("-3000", on=SEP20)
        with self.assertRaisesMessage(ValidationError, "remove them before clearing the day"):
            self.reconcile_from(None)
        with self.assertRaisesMessage(ValidationError, "the day comes after what was outstanding at it"):
            self.reconcile_from(SEP20)

    def test_nothing_new_is_dated_before_it_by_hand_by_a_payment_or_by_a_void(self):
        paid = self.disbursement("100", on=SEP10)
        self.reconcile_from(OCT1)
        with self.assertRaisesMessage(ValidationError, "nothing new is dated before it"):
            self.journal(SEP30, "Late rent", "-100", self.rent)
        with self.assertRaisesMessage(ValidationError, "nothing new is dated before it"):
            self.disbursement("100", on=SEP30)
        with self.assertRaisesMessage(ValidationError, "nothing new is dated before it"):
            paid.void(memo="Backdated", on_date=SEP20)
        paid.void(memo="Cancelled", on_date=OCT3)


class TheItemsTests(CutOffTestCase):
    """What may be listed as outstanding at the cut-off, and when it may change."""

    def setUp(self):
        super().setUp()
        self.september()

    def test_not_on_an_account_with_no_day(self):
        with self.assertRaisesMessage(ValidationError, "has no day its statements are reconciled from"):
            self.cheque("-3000")

    def test_a_plain_item_written_before_the_day_and_of_something(self):
        self.reconcile_from(OCT1)
        with self.assertRaisesMessage(ValidationError, "what was outstanding then was written before it"):
            self.cheque("-3000", on=OCT1)
        with self.assertRaisesMessage(ValidationError, "What the bank will show"):
            self.cheque("0")

    def test_a_payment_listed_is_posted_through_the_bank_before_the_day_standing_then_and_once(self):
        elsewhere = self.receipt("70", on=SEP10, account=self.other_bank)
        voided = self.disbursement("40", on=SEP10)
        voided.void(memo="Keyed twice", on_date=SEP20)
        self.reconcile_from(OCT1)
        after = self.disbursement("60", on=OCT3)
        for payment, said in ((elsewhere, "went through another account"),
                              (voided, "before the cut-off: it was not outstanding at it"),
                              (after, "what was outstanding then was written before it")):
            with self.subTest(payment=payment.number), self.assertRaisesMessage(ValidationError, said):
                BankCutOffItem.objects.create(bank_account=self.bank, payment=payment)
        item = BankCutOffItem.objects.create(bank_account=self.bank, payment=self.p2)
        self.assertEqual((item.date, item.amount, item.reference), (SEP25, D("-5000.00"), self.p2.number))
        with self.assertRaisesMessage(ValidationError, "is listed already"):
            BankCutOffItem.objects.create(bank_account=self.bank, payment=self.p2)
        with self.assertRaisesMessage(ValidationError, "a payment or an entry, not both"):
            BankCutOffItem.objects.create(bank_account=self.bank, payment=self.p3, entry=self.j1)

    def test_an_entry_listed_moved_money_and_stood_at_the_day(self):
        reversed_early = self.journal(SEP20, "Keyed twice", "-900", self.rent)
        reversal = reversed_early.create_reversal(entry_date=SEP28)
        self.reconcile_from(OCT1)
        for entry, said in ((self.p2.journal_entry, "a payment is listed as a payment"),
                            (reversed_early, f"was reversed by JE-{reversal.pk} on 2026-09-28")):
            with self.subTest(entry=entry.pk), self.assertRaisesMessage(ValidationError, said):
                BankCutOffItem.objects.create(bank_account=self.bank, entry=entry)
        item = BankCutOffItem.objects.create(bank_account=self.bank, entry=self.j1)
        self.assertEqual((item.date, item.amount, item.description), (SEP20, D("-8000.00"), "Rent"))

    def test_none_listed_changed_or_removed_while_a_statement_is_closed(self):
        self.reconcile_from(OCT1)
        listed = BankCutOffItem.objects.create(bank_account=self.bank, payment=self.p2)
        # Books 75,000 at the end, less the 5,000 cheque the bank has not paid: it holds 80,000.
        self.month(OCT1, OCT31, "80000", "80000").close()
        with self.assertRaisesMessage(ValidationError, "is closed and reads what was outstanding"):
            self.cheque("-3000")
        listed.description = "Renamed"
        with self.assertRaisesMessage(ValidationError, "is closed and reads what was outstanding"):
            listed.save()
        with self.assertRaisesMessage(ValidationError, "is closed and reads what was outstanding"):
            listed.delete()

    def test_matched_it_is_not_changed_or_removed(self):
        self.reconcile_from(OCT1)
        item = self.cheque("-3000")
        october = self.month(OCT1, OCT31, "0", "-3000")
        line = self.line(october, "-3000", on=OCT5)
        line.match_cut_off_item(item)
        item.reference = "000999"
        with self.assertRaisesMessage(ValidationError, "is matched to a line of"):
            item.save()
        with self.assertRaisesMessage(ValidationError, "is matched to a line of"):
            item.delete()
        line.unmatch()
        item.delete()


class MatchingFromTheDayTests(CutOffTestCase):
    def setUp(self):
        super().setUp()
        self.september()
        self.reconcile_from(OCT1)
        self.october = self.month(OCT1, OCT31, "0", "0")

    def test_what_moved_before_the_day_and_was_not_listed_is_not_matched_it_cleared(self):
        line = self.line(self.october, "-5000", on=OCT3)
        with self.assertRaisesMessage(ValidationError, "it cleared by then"):
            line.match(self.p2)
        with self.assertRaisesMessage(ValidationError, "it cleared by then"):
            line.match_entry(self.j1)
        BankCutOffItem.objects.create(bank_account=self.bank, payment=self.p2)
        line.match(self.p2)

    def test_a_plain_item_matched_at_its_own_amount_once_and_a_payment_item_as_the_payment(self):
        item = self.cheque("-3000")
        paid = BankCutOffItem.objects.create(bank_account=self.bank, payment=self.p2)
        wrong, line, again = (self.line(self.october, amount, on=OCT5) for amount in ("-3001", "-3000", "-3000"))
        with self.assertRaisesMessage(ValidationError, "Matching them would hide the difference"):
            wrong.match_cut_off_item(item)
        with self.assertRaisesMessage(ValidationError, "match the line to it as one"):
            wrong.match_cut_off_item(paid)
        line.match_cut_off_item(item)
        with self.assertRaisesMessage(ValidationError, "is already matched, on the statement"):
            again.match_cut_off_item(item)


class CancellingTests(CutOffTestCase):
    """A cheque from before the books, never presented, taken back by the journal that returned its money."""

    def setUp(self):
        super().setUp()
        self.september()
        self.early = self.journal(SEP30, "Put back early", "1500", self.ap)
        self.reconcile_from(OCT1)
        self.item = self.cheque("-1500", "000102")

    def test_by_an_entry_keyed_by_hand_after_the_day_putting_its_amount_back(self):
        wrong = self.journal(OCT20, "Wrong amount", "1400", self.ap)
        for entry, said in ((wrong, "through 1010 - Bank; taking back 000102 of"),
                            (self.early, "is dated before the cut-off"),
                            (self.p2.journal_entry, "is not an entry keyed by hand")):
            with self.subTest(entry=entry.pk), self.assertRaisesMessage(ValidationError, said):
                self.item.cancel(entry)
        back = self.journal(OCT20, "Cheque 000102 cancelled", "1500", self.ap)
        self.item.cancel(back)
        october = self.month(OCT1, OCT31, "86500", "86500")
        # The cheque and the entry that cancelled it are gone together; the wrong one waits for its line.
        self.assertEqual((october.outstanding_at_cut_off(), [entry for entry, _ in october.unpresented_entries()]),
                         ([], [wrong]))
        with self.assertRaisesMessage(ValidationError, "cancels 000102"):
            self.line(october, "1500", on=OCT20).match_entry(back)
        with self.assertRaisesMessage(ValidationError, "take the cancellation back there before reversing it"):
            back.reverse_by_hand()

    def test_not_once_matched_nor_by_an_entry_a_line_shows_nor_into_a_closed_month(self):
        october = self.month(OCT1, OCT31, "0", "0")
        back = self.journal(OCT20, "Cheque 000102 cancelled", "1500", self.ap)
        shown = self.line(october, "1500", on=OCT20)
        shown.match_entry(back)
        with self.assertRaisesMessage(ValidationError, "the bank showed it"):
            self.item.cancel(back)
        shown.unmatch()
        line = self.line(october, "-1500", on=OCT5)
        line.match_cut_off_item(self.item)
        with self.assertRaisesMessage(ValidationError, "is matched to a line of"):
            self.item.cancel(back)
        line.unmatch()
        self.item.cancel(back)
        self.item.refresh_from_db()
        self.item.uncancel()
        self.assertIsNone(BankCutOffItem.objects.get(pk=self.item.pk).cancelled_by_id)
        # Books 78,000 (75,000, the 1,500 put back early, the 1,500 put back now); the cheque outstanding and the
        # entry not yet shown net to nothing.
        shown.delete()
        line.delete()
        october.opening_balance = october.closing_balance = D("78000")
        october.save()
        october.close()
        with self.assertRaisesMessage(ValidationError, "is closed and reads what was outstanding"):
            self.item.cancel(back)


class ThroughTheOfficeTests(CutOffTestCase):
    """The bookkeeper sets the day, lists the cheque and matches it; the controller closes the month (O615)."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        self.clients = {}
        for role in ("Bookkeeper", "Controller", "Sales Rep"):
            user = User.objects.create_user(role.lower().replace(" ", "_"))
            user.groups.add(Group.objects.get(name=role))
            self.clients[role] = APIClient()
            self.clients[role].force_authenticate(user)
        self.september()

    def test_listed_matched_and_closed_by_the_people_who_do_it(self):
        bookkeeper, controller = self.clients["Bookkeeper"], self.clients["Controller"]
        answer = bookkeeper.patch(f"/api/accounting/accounts/{self.bank.pk}/", {"reconciled_from": "2026-10-01"},
                                  format="json")
        self.assertEqual((answer.status_code, answer.json().get("reconciled_from")), (200, "2026-10-01"),
                         answer.content)
        answer = bookkeeper.post("/api/accounting/bank-cut-off-items/", {
            "bank_account": self.bank.pk, "date": "2026-08-30", "reference": "000101", "description": "Old cheque",
            "amount": "-3000"}, format="json")
        self.assertEqual(answer.status_code, 201, answer.content)
        cheque = answer.json()["id"]
        answer = bookkeeper.post("/api/accounting/bank-cut-off-items/", {"bank_account": self.bank.pk,
                                                                           "payment": self.p2.pk}, format="json")
        self.assertEqual((answer.status_code, answer.json().get("amount"), answer.json().get("date")),
                         (201, "-5000.00", "2026-09-25"), answer.content)
        answer = bookkeeper.post("/api/accounting/bank-cut-off-items/", {"bank_account": self.bank.pk,
                                                                           "payment": self.p3.pk}, format="json")
        self.assertEqual(answer.status_code, 201, answer.content)
        refused = bookkeeper.post("/api/accounting/bank-cut-off-items/", {
            "bank_account": self.bank.pk, "date": "2026-10-02", "amount": "-10"}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("written before it", refused.content.decode())
        # Books 75,000, less the 5,000, 2,000 and 3,000 the bank had not paid: it held 85,000 at the cut-off.
        answer = bookkeeper.post("/api/accounting/bank-statements/", {
            "bank_account": self.bank.pk, "start_date": "2026-10-01", "end_date": "2026-10-31",
            "opening_balance": "85000", "closing_balance": "77000"}, format="json")
        self.assertEqual(answer.status_code, 201, answer.content)
        statement = answer.json()["id"]
        lines = {}
        for amount, on in (("-3000", "2026-10-05"), ("-5000", "2026-10-03")):
            answer = bookkeeper.post("/api/accounting/bank-statement-lines/", {
                "statement": statement, "date": on, "amount": amount}, format="json")
            self.assertEqual(answer.status_code, 201, answer.content)
            lines[amount] = answer.json()["id"]
        answer = bookkeeper.post(f"/api/accounting/bank-statements/{statement}/match/",
                                 {"line": lines["-3000"], "cut_off_item": cheque}, format="json")
        self.assertEqual(answer.status_code, 200, answer.content)
        answer = bookkeeper.post(f"/api/accounting/bank-statements/{statement}/match/",
                                 {"line": lines["-5000"], "payment": self.p2.pk}, format="json")
        self.assertEqual(answer.status_code, 200, answer.content)
        line = bookkeeper.get(f"/api/accounting/bank-statement-lines/{lines['-3000']}/").json()
        self.assertEqual((line["cut_off_item"], line["cut_off_item_name"], line["resolved"]),
                         (cheque, "000101 of -3000.00 on 2026-08-30", True))
        item = bookkeeper.get(f"/api/accounting/bank-cut-off-items/{cheque}/").json()
        self.assertEqual(item["shown_on"], "1010 to 31 Oct 2026")
        report = controller.get(f"/api/accounting/bank-statements/{statement}/reconciliation/").json()
        # Books 75,000; the 2,000 cheque still out; the bank 85,000 less the 8,000 it paid: 77,000.
        self.assertEqual((report["ledger_balance"], report["unpresented_total"], report["difference"],
                          report["reconciled"], [row["amount"] for row in report["unpresented"]]),
                         ("75000.00", "-2000.00", "0.00", True, ["-2000.00"]))
        self.assertEqual(bookkeeper.post(f"/api/accounting/bank-statements/{statement}/close/").status_code, 403)
        answer = controller.post(f"/api/accounting/bank-statements/{statement}/close/")
        self.assertEqual(answer.status_code, 200, answer.content)
        # The bookkeeper may not remove one at all; the controller may, but not under a closed month.
        self.assertEqual(bookkeeper.delete(f"/api/accounting/bank-cut-off-items/{cheque}/").status_code, 403)
        refused = controller.delete(f"/api/accounting/bank-cut-off-items/{cheque}/")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("is closed and reads what was outstanding", refused.content.decode())

    def test_a_cheque_cancelled_and_taken_back_through_the_office(self):
        bookkeeper = self.clients["Bookkeeper"]
        self.reconcile_from(OCT1)
        item = self.cheque("-1500", "000102")
        back = self.journal(OCT20, "Cheque 000102 cancelled", "1500", self.ap)
        answer = bookkeeper.post(f"/api/accounting/bank-cut-off-items/{item.pk}/cancel/", {"entry": back.pk},
                                 format="json")
        self.assertEqual((answer.status_code, answer.json().get("cancelled_by")), (200, back.pk), answer.content)
        listed = bookkeeper.get("/api/accounting/bank-cut-off-items/", {"cancelled_by__isnull": "true"}).json()
        self.assertEqual(listed, [])
        answer = bookkeeper.post(f"/api/accounting/bank-cut-off-items/{item.pk}/uncancel/")
        self.assertEqual((answer.status_code, answer.json().get("cancelled_by")), (200, None), answer.content)

    def test_who_may_read_them(self):
        self.reconcile_from(OCT1)
        self.cheque("-1500")
        self.assertEqual(self.clients["Sales Rep"].get("/api/accounting/bank-cut-off-items/").status_code, 403)
        self.assertEqual(self.clients["Bookkeeper"].get("/api/accounting/bank-cut-off-items/").status_code, 200)
        refused = self.clients["Sales Rep"].post("/api/accounting/bank-cut-off-items/", {
            "bank_account": self.bank.pk, "date": "2026-08-30", "amount": "-10"}, format="json")
        self.assertEqual(refused.status_code, 403)
