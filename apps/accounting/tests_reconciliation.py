"""
Bank reconciliation.

The control that proves the ledger matches reality. Everything else in
this system derives one number from another inside the same system; this
is the only place an outside source gets to disagree, and a
books-to-bank difference nobody has explained is how both fraud and
plain error stay invisible.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
)

from .models import (
    Account,
    AccountType,
    BankStatement,
    BankStatementLine,
    Payment,
    PaymentDirection,
)


class ReconciliationTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        UnitOfMeasure.objects.create(code="ea", name="Each")
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.bank = acc("1010", "Bank", AccountType.ASSET)
        self.other_bank = acc("1011", "Savings", AccountType.ASSET)
        self.ar = acc("1100", "AR", AccountType.ASSET)
        self.ap = acc("2000", "AP", AccountType.LIABILITY)
        self.charges = acc("5800", "Bank Charges", AccountType.EXPENSE)
        Company.objects.create(name="Test Co", base_currency=self.usd)

        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def receipt(self, amount, on=datetime.date(2026, 1, 5), account=None):
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=on, amount=Decimal(amount), currency=self.usd,
            bank_account=account or self.bank, counterpart_account=self.ar,
        )
        payment.post()
        return payment

    def disbursement(self, amount, on=datetime.date(2026, 1, 6)):
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.DISBURSEMENT,
            payment_date=on, amount=Decimal(amount), currency=self.usd,
            bank_account=self.bank, counterpart_account=self.ap,
        )
        payment.post()
        return payment

    def statement(self, opening="0", closing="0"):
        return BankStatement.objects.create(
            bank_account=self.bank, start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 1, 31),
            opening_balance=Decimal(opening), closing_balance=Decimal(closing),
        )

    def line(self, statement, amount, on=datetime.date(2026, 1, 5), **kwargs):
        return BankStatementLine.objects.create(
            statement=statement, date=on, amount=Decimal(amount), **kwargs
        )


class StatementIntegrityTests(ReconciliationTestCase):
    def test_a_statement_that_does_not_foot_is_caught(self):
        """Keyed in wrong. No point reconciling against it until fixed."""
        statement = self.statement("0", "1000")
        self.line(statement, "900")

        self.assertEqual(statement.statement_difference(), Decimal("100"))
        with self.assertRaisesMessage(ValidationError, "Fix the statement"):
            statement.close()

    def test_a_statement_that_foots_passes_that_check(self):
        statement = self.statement("0", "1000")
        self.line(statement, "1000")
        self.assertEqual(statement.statement_difference(), Decimal("0"))

    def test_it_cannot_end_before_it_starts(self):
        statement = BankStatement(
            bank_account=self.bank, start_date=datetime.date(2026, 2, 1),
            end_date=datetime.date(2026, 1, 1),
            opening_balance=Decimal("0"), closing_balance=Decimal("0"),
        )
        with self.assertRaisesMessage(ValidationError, "cannot end before it starts"):
            statement.clean()


class MatchingTests(ReconciliationTestCase):
    def test_a_matched_line_is_explained(self):
        payment = self.receipt("1000")
        statement = self.statement("0", "1000")
        line = self.line(statement, "1000")

        line.match(payment)

        self.assertTrue(line.is_resolved())
        self.assertTrue(payment.is_reconciled())

    def test_the_sign_has_to_agree(self):
        """A bank showing money out cannot be a receipt."""
        payment = self.receipt("1000")
        statement = self.statement("0", "-1000")
        line = self.line(statement, "-1000")

        with self.assertRaisesMessage(ValidationError, "Matching them would hide"):
            line.match(payment)

    def test_the_amount_has_to_agree(self):
        payment = self.receipt("1000")
        statement = self.statement("0", "999")
        line = self.line(statement, "999")

        with self.assertRaisesMessage(ValidationError, "Matching them would hide"):
            line.match(payment)

    def test_another_account_s_payment_cannot_match(self):
        payment = self.receipt("1000", account=self.other_bank)
        statement = self.statement("0", "1000")
        line = self.line(statement, "1000")

        with self.assertRaisesMessage(ValidationError, "different bank account"):
            line.match(payment)

    def test_a_voided_payment_never_reached_the_bank(self):
        payment = self.receipt("1000")
        payment.void()
        statement = self.statement("0", "1000")
        line = self.line(statement, "1000")

        with self.assertRaisesMessage(ValidationError, "never reached the bank"):
            line.match(payment)

    def test_a_payment_can_only_be_matched_once(self):
        payment = self.receipt("1000")
        first = self.statement("0", "1000")
        self.line(first, "1000").match(payment)

        second = BankStatement.objects.create(
            bank_account=self.bank, start_date=datetime.date(2026, 2, 1),
            end_date=datetime.date(2026, 2, 28),
            opening_balance=Decimal("1000"), closing_balance=Decimal("2000"),
        )
        line = self.line(second, "1000", on=datetime.date(2026, 2, 5))

        with self.assertRaises(Exception):
            line.match(payment)

    def test_a_line_can_be_unmatched(self):
        payment = self.receipt("1000")
        statement = self.statement("0", "1000")
        line = self.line(statement, "1000")
        line.match(payment)

        line.unmatch()

        self.assertFalse(line.is_resolved())


class AutoMatchTests(ReconciliationTestCase):
    def test_it_matches_the_obvious_ones(self):
        self.receipt("1000", on=datetime.date(2026, 1, 5))
        self.disbursement("250", on=datetime.date(2026, 1, 6))
        statement = self.statement("0", "750")
        self.line(statement, "1000", on=datetime.date(2026, 1, 6))
        self.line(statement, "-250", on=datetime.date(2026, 1, 7))

        matched = statement.auto_match()

        self.assertEqual(len(matched), 2)
        self.assertEqual(statement.unresolved_lines(), [])

    def test_two_candidates_means_neither_is_taken(self):
        """An automatic match that is wrong is worse than no match,
        because nobody looks at it again."""
        self.receipt("500", on=datetime.date(2026, 1, 5))
        self.receipt("500", on=datetime.date(2026, 1, 6))
        statement = self.statement("0", "500")
        self.line(statement, "500", on=datetime.date(2026, 1, 6))

        matched = statement.auto_match()

        self.assertEqual(matched, [])
        self.assertEqual(len(statement.unresolved_lines()), 1)

    def test_a_distant_date_is_not_matched(self):
        self.receipt("1000", on=datetime.date(2026, 1, 1))
        statement = self.statement("0", "1000")
        self.line(statement, "1000", on=datetime.date(2026, 1, 30))

        self.assertEqual(statement.auto_match(tolerance_days=5), [])


class BankOriginatedTests(ReconciliationTestCase):
    """The handful of lines the bank starts — charges, interest. Without
    a way to post them, reconciliation stalls on exactly the entries
    nobody would otherwise record."""

    def test_a_bank_charge_can_be_posted_straight_out(self):
        statement = self.statement("0", "-15")
        line = self.line(statement, "-15", on=datetime.date(2026, 1, 31))

        line.post_to(self.charges, memo="Quarterly account fee")

        self.assertTrue(line.is_resolved())
        self.assertEqual(
            line.journal_entry.lines.get(account=self.charges).debit, Decimal("15")
        )
        self.assertEqual(
            line.journal_entry.lines.get(account=self.bank).credit, Decimal("15")
        )

    def test_interest_received_goes_the_other_way(self):
        interest = Account.objects.create(
            code="4800", name="Interest", account_type=AccountType.INCOME
        )
        statement = self.statement("0", "12")
        line = self.line(statement, "12")

        line.post_to(interest)

        self.assertEqual(line.journal_entry.lines.get(account=self.bank).debit, Decimal("12"))
        self.assertEqual(line.journal_entry.lines.get(account=interest).credit, Decimal("12"))

    def test_an_explained_line_cannot_be_explained_twice(self):
        statement = self.statement("0", "-15")
        line = self.line(statement, "-15")
        line.post_to(self.charges)

        with self.assertRaisesMessage(ValidationError, "already explained"):
            line.post_to(self.charges)


class ReconcileTests(ReconciliationTestCase):
    def test_a_clean_period_reconciles_and_closes(self):
        payment = self.receipt("1000")
        statement = self.statement("0", "1000")
        self.line(statement, "1000").match(payment)

        report = statement.reconciliation()
        self.assertEqual(report["difference"], Decimal("0"))
        self.assertTrue(statement.is_reconciled())

        statement.close()
        self.assertTrue(statement.closed)

    def test_an_uncashed_cheque_is_a_named_reconciling_item(self):
        """The legitimate reason the books and the bank differ. Naming it
        is the difference between a reconciliation and a shrug."""
        received = self.receipt("1000")
        self.disbursement("300", on=datetime.date(2026, 1, 20))
        statement = self.statement("0", "1000")
        self.line(statement, "1000").match(received)

        report = statement.reconciliation()

        self.assertEqual(report["ledger_balance"], Decimal("700"))
        self.assertEqual(report["statement_balance"], Decimal("1000"))
        self.assertEqual(report["unpresented_total"], Decimal("-300"))
        self.assertEqual(report["difference"], Decimal("0"))
        self.assertEqual(len(report["unpresented"]), 1)

    def test_an_unexplained_line_blocks_closing(self):
        statement = self.statement("0", "40")
        self.line(statement, "40")

        with self.assertRaisesMessage(ValidationError, "still unexplained"):
            statement.close()

    def test_a_real_difference_blocks_closing(self):
        """A reconciliation that closes over a difference is not a
        reconciliation."""
        statement = self.statement("0", "500")
        line = self.line(statement, "500")
        line.post_to(self.charges)
        # The posting moved the ledger with it, so force a mismatch by
        # recording money the bank never saw on another account.
        self.receipt("125", on=datetime.date(2026, 1, 9), account=self.other_bank)
        statement.closing_balance = Decimal("600")
        statement.save()

        with self.assertRaises(ValidationError):
            statement.close()

    def test_a_closed_statement_is_frozen(self):
        payment = self.receipt("1000")
        statement = self.statement("0", "1000")
        self.line(statement, "1000").match(payment)
        statement.close()

        with self.assertRaisesMessage(ValidationError, "statement is closed"):
            self.line(statement, "50")

    def test_it_can_be_reopened(self):
        payment = self.receipt("1000")
        statement = self.statement("0", "1000")
        self.line(statement, "1000").match(payment)
        statement.close()

        statement.reopen()

        self.assertFalse(statement.closed)
        self.line(statement, "50")  # must not raise

    def test_it_cannot_be_closed_twice(self):
        payment = self.receipt("1000")
        statement = self.statement("0", "1000")
        self.line(statement, "1000").match(payment)
        statement.close()

        with self.assertRaisesMessage(ValidationError, "already closed"):
            statement.close()
