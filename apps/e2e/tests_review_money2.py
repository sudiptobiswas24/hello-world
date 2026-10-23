"""Review of fix_money2: races on PostgreSQL."""
import datetime
from decimal import Decimal

from .tests_races import RaceCase, race

FILE_A = "Date,Narration,Ref No,Withdrawal,Deposit\n05-09-2026,NEFT in,UTR1,,500.00\n06-09-2026,Charges,C1,10.00,\n"
FILE_B = "Date,Narration,Ref No,Withdrawal,Deposit\n06-09-2026,Charges,C1,10.00,\n07-09-2026,Rent,R1,100.00,\n"


class BankRaces(RaceCase):
    def build(self):
        from apps.accounting.models import Account, AccountType, BankStatement
        from apps.core.models import Company, Currency

        inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Company.objects.create(name="Deccan Polysacks", base_currency=inr)
        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        return BankStatement.objects.create(
            bank_account=bank, start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 30),
            opening_balance=Decimal("0"), closing_balance=Decimal("490.00"))

    def test_two_overlapping_files_at_once(self):
        from apps.accounting.bank_import import import_lines
        from apps.accounting.models import BankStatement, BankStatementLine

        st = self.build()
        a = lambda: import_lines(BankStatement.objects.get(pk=st.pk), FILE_A, commit=True)  # noqa: E731
        b = lambda: import_lines(BankStatement.objects.get(pk=st.pk), FILE_B, commit=True)  # noqa: E731
        out = race(BankStatementLine, a, b)
        n = BankStatementLine.objects.filter(statement=st).count()
        print("OVERLAP outcomes", out, "lines", n)
        self.assertEqual(n, 3)

    def test_import_and_close_at_once(self):
        from apps.accounting.bank_import import import_lines
        from apps.accounting.models import BankStatement, BankStatementLine

        st = self.build()
        a = lambda: import_lines(BankStatement.objects.get(pk=st.pk), FILE_A, commit=True)  # noqa: E731
        c = lambda: BankStatement.objects.get(pk=st.pk).close()  # noqa: E731
        out = race(BankStatementLine, a, c)
        st.refresh_from_db()
        n = BankStatementLine.objects.filter(statement=st).count()
        print("CLOSE outcomes", out, "closed", st.closed, "lines", n)
